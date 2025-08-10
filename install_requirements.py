import subprocess
import sys

packages = [
    "attrs==25.3.0",
    "certifi==2025.8.3",
    "cffi==1.17.1",
    "charset-normalizer==3.4.2",
    "decorator==4.4.2",
    "exporter==0.0.4",
    "fastjsonschema==2.21.1",
    "glcontext==3.0.0",
    "idna==3.10",
    "imageio==2.37.0",
    "imageio-ffmpeg==0.6.0",
    "imgkit==1.2.3",
    "jsonschema==4.25.0",
    "jsonschema-specifications==2025.4.1",
    "jupyter_core==5.8.1",
    "moderngl==5.12.0",
    "moviepy==1.0.3",
    "nbformat==5.10.4",
    "numpy==2.3.2",
    "pillow==11.3.0",
    "pip==25.2",
    "platformdirs==4.3.8",
    "proglog==0.1.12",
    "pycparser==2.22",
    "Pygments==2.19.2",
    "PySide6==6.9.1",
    "PySide6_Addons==6.9.1",
    "PySide6_Essentials==6.9.1",
    "referencing==0.36.2",
    "requests==2.32.4",
    "rpds-py==0.27.0",
    "scipy==1.16.1",
    "setuptools==80.9.0",
    "shiboken6==6.9.1",
    "six==1.17.0",
    "sounddevice==0.5.2",
    "soundfile==0.13.1",
    "tqdm==4.67.1",
    "traitlets==5.14.3",
    "urllib3==2.5.0",
]

def main():
    subprocess.check_call([sys.executable, "-m", "pip", "install", *packages])

if __name__ == "__main__":
    main()
