from pathlib import Path

root = Path(SPECPATH)
analysis = Analysis([str(root / "launch.py")], pathex=[str(root)],
                    hiddenimports=["tkinter", "tkinter.ttk", "numpy", "tifffile"],
                    excludes=["matplotlib", "scipy", "pandas", "rawpy", "IPython", "pytest"])
archive = PYZ(analysis.pure)
gui = EXE(archive, analysis.scripts, [], exclude_binaries=True,
          name="NRAW-to-CinemaDNG", console=False, upx=False)
worker = EXE(archive, analysis.scripts, [], exclude_binaries=True,
             name="NRAW-to-CinemaDNG-worker", console=True, upx=False)
bundle = COLLECT(gui, worker, analysis.binaries, analysis.datas, upx=False,
                 name="NRAW-to-CinemaDNG")
