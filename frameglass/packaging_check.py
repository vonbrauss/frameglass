"""Explicit, isolated native smoke check for a packaged build (no user settings)."""
import json,sys,time,ssl,os
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings,QTimer
from PySide6.QtGui import QColorSpace,QImage
from .app import Window,STYLE
from .color import DisplayTransform,rgb_image
from .core import executable


def main():
    root=Path(sys.argv[2]).absolute();root.mkdir(parents=True,exist_ok=True)
    result={'frozen':bool(getattr(sys,'frozen',False)),'executable':sys.executable,'checks':[]}
    app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
    settings=QSettings(str(root/'settings.ini'),QSettings.IniFormat);settings.clear();settings.setValue('volume',0)
    w=Window(settings);w.show();w.inspect(str(root/'clip.mp4'));state={'phase':'video','deadline':time.monotonic()+40}
    def advance(phase):state.update(phase=phase,deadline=time.monotonic()+30)
    def tick():
        try:
            if time.monotonic()>state['deadline']:raise RuntimeError('Timed out: '+state['phase'])
            phase=state['phase'];p=w.playback
            if phase=='video' and w.data and p:
                assert w.data['streams'];p.toggle();advance('play')
            elif phase=='play' and p.frame_seen and p.player.position()>200:
                assert p.audio is not None;assert p.audio_frame_seen
                result['checks'].append('Bundled Qt FFmpeg video/audio playback and QML AudioOutput')
                w.delivery.presets.setCurrentIndex(1);w.delivery.highlight.setChecked(True);w.delivery.show_details();assert w.delivery.popup.isVisible();w.delivery.popup.hide()
                w.grab().save(str(root/'video.png'));w.inspect(str(root/'still.jpg'));advance('still')
            elif phase=='still' and w.data:
                assert w.data['kind']=='still';assert not w.still_image.isNull()
                import numpy as np
                converted=rgb_image(np.full((32,32,3),.5,np.float32),DisplayTransform('bt2020','arib-std-b67','bt2020nc',False))
                assert not converted.isNull();assert converted.colorSpace()==QColorSpace(QColorSpace.SRgb)
                result['checks'].append('Pillow still decoding and NumPy HDR conversion')
                w.inspect(str(root/'audio.wav'));advance('audio')
            elif phase=='audio' and w.data and p:
                p.show_audio_channels();p.channel_panel.solo[0].setChecked(True);p.toggle();advance('monitor')
            elif phase=='monitor' and p.monitor.bytes_written>16000:
                assert p.audio.isMuted();assert len(p.channel_panel.labels)==2
                result['checks'].append('Native stereo channel auditioning')
                w.open_audio_analysis();advance('analysis')
            elif phase=='analysis' and w.analysis_summary:
                result['checks'].append('External FFmpeg full-track audio analysis')
                assert Path(os.environ['SSL_CERT_FILE']).is_file();assert ssl.create_default_context().cert_store_stats()['x509_ca']>0
                if result['frozen']:assert Path(os.environ['SSL_CERT_FILE']).resolve().is_relative_to(Path(sys.executable).resolve().parents[2])
                result['checks'].append('Bundled HTTPS certificate trust store')
                w.analysis_dialog.close();w.open_quality(2);assert w.quality_workspace.isVisible();w.quality_workspace.close()
                result['checks'].append('Delivery preset workspace and highlight dropdown')
                result['ffprobe']=executable('ffprobe');result['ffmpeg']=executable('ffmpeg');result['status']='PASS';finish(0)
        except Exception as error:
            result['status']='FAIL';result['error']=repr(error);finish(1)
    def finish(code):
        timer.stop();(root/'result.json').write_text(json.dumps(result,indent=2));w.close();QTimer.singleShot(350,lambda:app.exit(code))
    timer=QTimer();timer.timeout.connect(tick);timer.start(100);sys.exit(app.exec())
