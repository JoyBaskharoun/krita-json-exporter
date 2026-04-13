from krita import Krita
from .exporter import LottieExportExtension

instance = LottieExportExtension(Krita.instance())
Krita.instance().addExtension(instance)
