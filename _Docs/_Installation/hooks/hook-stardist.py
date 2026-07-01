# hook-stardist.py
# Verified against stardist-0.9.1-cp310-cp310-macosx_12_0_arm64.whl

from PyInstaller.utils.hooks import collect_data_files

hiddenimports = [
    "stardist",
    "stardist.big",
    "stardist.bioimageio_utils",
    "stardist.geometry",
    "stardist.geometry.geom2d",
    "stardist.geometry.geom3d",
    "stardist.matching",
    "stardist.models",
    "stardist.models.base",
    "stardist.models.model2d",
    "stardist.models.model3d",
    "stardist.nms",
    "stardist.plot",
    "stardist.plot.plot",
    "stardist.plot.render",
    "stardist.rays3d",
    "stardist.sample_patches",
    "stardist.scripts",
    "stardist.scripts.predict2d",
    "stardist.scripts.predict3d",
    "stardist.utils",
    "stardist.version",
]

datas = collect_data_files("stardist", includes=["**/*"])
