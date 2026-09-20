# -*- mode: python ; coding: utf-8 -*-
"""
Сборка Windows-приложения: pyinstaller midi2tab.spec

Тяжёлый TensorFlow исключён намеренно -- распознавание работает на
ONNX-модели весом 225 КБ. Если его не исключить, .exe раздувается
примерно на 600 МБ без всякой пользы.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = []
binaries = []
hiddenimports = ["guitarpro", "pretty_midi", "pygame", "pygame.midi"]

# Разделение дорожек -- только если demucs установлен (тянет PyTorch,
# поэтому .exe с ним получается очень большим; без него сборка лёгкая)
try:
    import demucs  # noqa: F401

    datas += collect_data_files("demucs")
    hiddenimports += ["demucs", "demucs.separate", "torch", "torchaudio"]
except ImportError:
    pass

# Модель распознавания и её окружение -- только если basic-pitch установлен
try:
    import basic_pitch  # noqa: F401

    datas += collect_data_files("basic_pitch", includes=["saved_models/**/*.onnx"])
    binaries += collect_dynamic_libs("onnxruntime")
    hiddenimports += ["onnxruntime", "basic_pitch.inference", "librosa", "soundfile"]
    datas += collect_data_files("librosa")
    datas += collect_data_files("soundfile")
except ImportError:
    pass

a = Analysis(
    ["run_gui.pyw"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "tensorflow",
        "tensorflow.python",
        "keras",
        "tensorboard",
        "coremltools",
        "matplotlib",
        "IPython",
        "notebook",
        "pandas",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MidiToTab",
    console=False,          # без чёрного окна консоли
    disable_windowed_traceback=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="MidiToTab",
)
