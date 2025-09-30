#!/usr/bin/env python3
# Copyright (C) Alibaba Group. All Rights Reserved.
# MIT License (https://opensource.org/licenses/MIT)

# I add new functions related to threading management based on their original code.


import subprocess
import threading
import pyaudio


class RealtimeMp3Player:
    """
    实时MP3播放器类 
    该类使用ffmpeg将MP3数据解码为PCM格式，并通过PyAudio实时播放音频
    """
    def __init__(self, verbose=False):
        """
        初始化RealtimeMp3Player实例
        
        Args:
            verbose (bool): 是否输出详细日志信息，默认为False
        """
        self.ffmpeg_process = None  # ffmpeg进程，用于解码MP3
        self._stream = None         # PyAudio流对象，用于播放音频
        self._player = None         # PyAudio实例
        self.play_thread = None     # 播放线程
        self.stop_event = threading.Event()  # 停止事件，用于线程同步
        self.verbose = verbose      # 是否输出详细日志
        
        # 添加状态标志，防止重复清理
        self._is_stopped = False
        self._cleanup_lock = threading.Lock()
        
        # Add internal state tracking
        self._audio_buffer_size = 0  # Track approximate buffer size
        self._buffer_lock = threading.Lock()  # Lock for buffer operations

    def reset(self):
        """
        重置播放器状态，将所有属性重新初始化为None或初始状态
        """
        with self._cleanup_lock:
            self.ffmpeg_process = None
            self._stream = None
            self._player = None
            self.play_thread = None
            self.stop_event = threading.Event()
            self._is_stopped = False
            self._audio_buffer_size = 0

    def start(self):
        """
        启动播放器，初始化PyAudio和ffmpeg进程
        """
        with self._cleanup_lock:
            if not self._is_stopped:
                self._player = pyaudio.PyAudio()  # 初始化PyAudio以播放音频
                # 打开PyAudio流，设置音频参数：16位整数格式，单声道，采样率22050Hz
                self._stream = self._player.open(
                    format=pyaudio.paInt16, channels=1, rate=22050,
                    output=True)
                try:
                    # 初始化ffmpeg进程，用于将MP3解码为PCM数据
                    self.ffmpeg_process = subprocess.Popen(
                        [
                            'ffmpeg', '-i', 'pipe:0', '-f', 's16le', '-ar', '22050',
                            '-ac', '1', 'pipe:1'
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                    if self.verbose:
                        print('mp3 audio player is started')
                except subprocess.CalledProcessError as e:
                    print(f'An error occurred: {e}')

    def stop(self):
        """正常停止播放器"""
        with self._cleanup_lock:
            # 如果已经停止，直接返回
            if self._is_stopped:
                if self.verbose:
                    print("mp3 audio player already stopped, skipping")
                return
            
            self._is_stopped = True
            
            try:
                # 1. 设置停止事件
                self.stop_event.set()
                
                # 2. 停止并等待播放线程
                if self.play_thread and self.play_thread.is_alive():
                    self.play_thread.join(timeout=1)
                    self.play_thread = None
                
                # 3. 关闭ffmpeg进程
                if self.ffmpeg_process:
                    if self.ffmpeg_process.stdin:
                        try:
                            self.ffmpeg_process.stdin.close()
                        except Exception:
                            pass
                    try:
                        self.ffmpeg_process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.ffmpeg_process.terminate()
                        try:
                            self.ffmpeg_process.wait(timeout=0.5)
                        except subprocess.TimeoutExpired:
                            self.ffmpeg_process.kill()
                    self.ffmpeg_process = None

                # 4. 停止并关闭PyAudio流
                if self._stream:
                    try:
                        if self._stream.is_active():
                            self._stream.stop_stream()
                        self._stream.close()
                    except Exception as e:
                        if self.verbose:
                            print(f"Error closing stream: {e}")
                    self._stream = None

                # 5. 终止PyAudio实例
                if self._player:
                    try:
                        self._player.terminate()
                    except Exception as e:
                        if self.verbose:
                            print(f"Error terminating player: {e}")
                    self._player = None

                if self.verbose:
                    print("mp3 audio player is stopped")
                    
            except Exception as e:
                print(f"stop error: {e}")

    def play_audio(self):
        """
        播放音频数据
        从ffmpeg进程中读取解码后的PCM数据并播放
        """
        try:
            # 持续读取和播放音频数据直到停止事件被设置
            while not self.stop_event.is_set():
                # 检查进程和流是否还存在
                if not self.ffmpeg_process or not self.ffmpeg_process.stdout:
                    break
                if not self._stream:
                    break
                    
                try:
                    # 使用更小的块大小以便更快响应停止信号
                    pcm_data = self.ffmpeg_process.stdout.read(512)  # 从1024减少到512
                except Exception as e:
                    if self.verbose:
                        print(f"Error reading from ffmpeg: {e}")
                    break
                    
                if pcm_data:
                    # 在写入前再次检查停止标志
                    if self.stop_event.is_set():
                        break
                    try:
                        # 将PCM数据写入PyAudio流进行播放
                        self._stream.write(pcm_data)
                        
                        # Update buffer size (decrement as we play)
                        with self._buffer_lock:
                            self._audio_buffer_size = max(0, self._audio_buffer_size - len(pcm_data))
                            
                    except Exception as e:
                        if self.verbose:
                            print(f"Error writing to stream: {e}")
                        break
                else:
                    # 如果没有更多数据，则退出循环
                    break
        except Exception as e:
            print(f'play_audio error: {e}')
        finally:
            if self.verbose:
                print('play_audio thread exited')

    def write(self, data: bytes) -> None:
        """
        向播放器写入MP3音频数据
        
        Args:
            data (bytes): MP3格式的音频数据
        """
        # 检查是否已停止
        if self._is_stopped or self.stop_event.is_set():
            return
            
        try:
            if self.ffmpeg_process and self.ffmpeg_process.stdin:
                # 将MP3数据写入ffmpeg的标准输入进行解码
                self.ffmpeg_process.stdin.write(data)
                
                # Update buffer size (increment as we add data)
                with self._buffer_lock:
                    self._audio_buffer_size += len(data)
                
                if self.play_thread is None:
                    # 如果播放线程尚未启动，则初始化并启动它
                    if self._stream:
                        self._stream.start_stream()  # 启动音频流
                    # 创建并启动播放线程
                    self.play_thread = threading.Thread(target=self.play_audio)
                    self.play_thread.start()
        except Exception as e:
            if self.verbose:
                print(f'write error: {e}')

    def is_playing(self) -> bool:
        """
        检查播放器是否还在播放音频
        
        Returns:
            bool: 如果播放器还在处理或播放音频则返回True，否则返回False
        """
        # 如果已经停止，直接返回False
        if self._is_stopped:
            return False
        
        # 检查是否有播放线程在运行
        if self.play_thread and self.play_thread.is_alive():
            return True
        
        # 检查ffmpeg进程是否还在运行
        if self.ffmpeg_process:
            try:
                # 检查进程是否还活着
                return self.ffmpeg_process.poll() is None
            except:
                return False
        
        # 检查PyAudio流是否还活跃
        if self._stream:
            try:
                return self._stream.is_active()
            except:
                pass
        
        # 检查内部缓冲区是否还有数据
        with self._buffer_lock:
            return self._audio_buffer_size > 0
        
        return False

    def get_buffer_status(self) -> dict:
        """
        获取播放器缓冲区状态
        
        Returns:
            dict: 包含缓冲区状态信息的字典
        """
        with self._buffer_lock:
            buffer_size = self._audio_buffer_size
            
        return {
            "is_playing": self.is_playing(),
            "buffer_size": buffer_size,
            "ffmpeg_process_active": self.ffmpeg_process and self.ffmpeg_process.poll() is None,
            "play_thread_alive": self.play_thread and self.play_thread.is_alive(),
            "stream_active": self._stream and self._stream.is_active() if self._stream else False,
            "is_stopped": self._is_stopped
        }

    def force_stop(self):
        """
        立即强制停止播放，类似 Ctrl+C。
        会终止 ffmpeg、关闭 PyAudio 流和线程。
        """
        with self._cleanup_lock:
            # 如果已经停止，直接返回
            if self._is_stopped:
                if self.verbose:
                    print("mp3 audio player already force stopped, skipping")
                return
            
            self._is_stopped = True
            
            try:
                # 1. 设置停止事件，让播放线程知道要退出
                self.stop_event.set()

                # 2. 强制终止 ffmpeg 进程
                if self.ffmpeg_process:
                    try:
                        self.ffmpeg_process.kill()
                        self.ffmpeg_process.wait(timeout=0.5)
                    except Exception:
                        pass
                    self.ffmpeg_process = None

                # 3. 停止播放线程
                if self.play_thread and self.play_thread.is_alive():
                    self.play_thread.join(timeout=1)
                    self.play_thread = None

                # 4. 停止并关闭 PyAudio 流
                if self._stream:
                    try:
                        if self._stream.is_active():
                            self._stream.stop_stream()
                        self._stream.close()
                    except Exception as e:
                        if self.verbose:
                            print(f"Error force closing stream: {e}")
                    self._stream = None

                # 5. 终止 PyAudio 实例
                if self._player:
                    try:
                        self._player.terminate()
                    except Exception as e:
                        if self.verbose:
                            print(f"Error force terminating player: {e}")
                    self._player = None

                # Reset buffer size
                with self._buffer_lock:
                    self._audio_buffer_size = 0

                if self.verbose:
                    print("mp3 audio player is force stopped")
                    
            except Exception as e:
                print(f"force_stop error: {e}")
