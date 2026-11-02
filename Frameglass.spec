# Build with: .venv/bin/python -m PyInstaller --noconfirm --clean Frameglass.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files,copy_metadata
root=Path(SPECPATH)
a=Analysis([str(root/'packaging'/'launcher.py')],pathex=[str(root)],
    binaries=[],datas=collect_data_files('certifi')+copy_metadata('Pillow')+[
        (str(root/'docs'),'docs'),(str(root/'build'/'third-party-licenses'),'third-party-licenses'),(str(root/'packaging'/'THIRD_PARTY_NOTICES.md'),'.')],
    hiddenimports=['certifi'],hookspath=[str(root/'packaging'/'hooks')],
    runtime_hooks=[str(root/'packaging'/'runtime.py')],
    excludes=['tkinter','unittest','pytest','PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtWebEngineQuick'],
    noarchive=False,optimize=0)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='Frameglass',debug=False,bootloader_ignore_signals=False,
    strip=False,upx=False,console=False,argv_emulation=False,target_arch='arm64',codesign_identity=None,entitlements_file=None)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='Frameglass')
app=BUNDLE(coll,name='Frameglass.app',icon=str(root/'assets'/'Frameglass.icns'),
    bundle_identifier='com.frameglass.media-inspector',version='0.1.0',
    info_plist={'CFBundleDisplayName':'Frameglass','CFBundleShortVersionString':'0.1.0','CFBundleVersion':'1',
        'NSHighResolutionCapable':True,'NSPrincipalClass':'NSApplication','LSApplicationCategoryType':'public.app-category.video',
        'NSHumanReadableCopyright':'Frameglass · Built with Python, Qt for Python, NumPy and Pillow.',
        'CFBundleDocumentTypes':[{'CFBundleTypeName':'Media file','CFBundleTypeRole':'Viewer','LSHandlerRank':'Alternate',
                                 'LSItemContentTypes':['public.movie','public.audio','public.image']}],
        'LSMinimumSystemVersion':'13.0'})
