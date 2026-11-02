"""Copy installed component notices into the standalone bundle."""
import importlib.metadata as metadata
import shutil,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];output=root/'build'/'third-party-licenses';output.mkdir(parents=True,exist_ok=True)
for name in ('numpy','pillow','certifi','pyside6','pyside6_addons','pyside6_essentials','shiboken6','pyinstaller'):
    distribution=metadata.distribution(name);folder=output/name;folder.mkdir(exist_ok=True)
    (folder/'VERSION.txt').write_text(f'{distribution.metadata["Name"]} {distribution.version}\n')
    for path in distribution.files or []:
        if any(token in str(path).lower() for token in ('license','copying','copyright')):
            source=Path(distribution.locate_file(path))
            if source.is_file():
                destination=folder/str(path).replace('../','');destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
for parent in [Path(sys.base_prefix),*list(Path(sys.base_prefix).parents)[:5]]:
    python_license=parent/'LICENSE'
    if python_license.is_file():shutil.copy2(python_license,output/'PYTHON-LICENSE.txt');break
extra=root/'packaging'/'licenses'
if extra.exists():shutil.copytree(extra,output/'qt-ffmpeg',dirs_exist_ok=True)

import json,platform
(output/'BUILD-INFO.json').write_text(json.dumps({'python':sys.version,'architecture':platform.machine(),'build_system':platform.platform(),
    'packages':{name:metadata.version(name) for name in ('pyside6','numpy','pillow','certifi','pyinstaller','pyinstaller-hooks-contrib')}},indent=2))
