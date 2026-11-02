"""Collect only the QML modules needed by native AudioOutput."""
from pathlib import Path,PurePath
from PyInstaller.utils.hooks.qt import add_qt6_dependencies,pyside6_library_info
hiddenimports,binaries,datas=add_qt6_dependencies(__file__)
source=Path(pyside6_library_info.location['QmlImportsPath'])
destination=PurePath(pyside6_library_info.qt_rel_dir)/'qml'
for module in ('QtQml','QtQml/Models','QtQml/WorkerScript','QtQuick','QtMultimedia'):
    module_binaries,module_datas=pyside6_library_info._process_qml_plugin(source/module/'qmldir')
    for items,output in ((module_binaries,binaries),(module_datas,datas)):
        for item in items:
            relative=item.relative_to(source)
            output.append((str(item),str(destination/(relative if item.is_dir() else relative.parent))))
