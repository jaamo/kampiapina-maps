"""Pipe raw frames into the ffmpeg binary bundled with imageio-ffmpeg."""

import subprocess

import imageio_ffmpeg


class VideoWriter:
    def __init__(self, path: str, width: int, height: int, fps: int, crf: int = 16):
        cmd = [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", path,
        ]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def write(self, surface):
        # cairo RGB24 is 32 bits per pixel, B G R x in memory on little-endian machines.
        self.proc.stdin.write(bytes(surface.get_data()))

    def close(self):
        self.proc.stdin.close()
        if self.proc.wait() != 0:
            raise RuntimeError("ffmpeg failed")
