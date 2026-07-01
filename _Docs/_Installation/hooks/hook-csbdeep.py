# hook-csbdeep.py
# Verified against csbdeep-0.8.0-py2.py3-none-any.whl

from PyInstaller.utils.hooks import collect_data_files

hiddenimports = [
    "csbdeep",
    "csbdeep.data",
    "csbdeep.data.generate",
    "csbdeep.data.prepare",
    "csbdeep.data.rawdata",
    "csbdeep.data.transform",
    "csbdeep.internals",
    "csbdeep.internals.blocks",
    "csbdeep.internals.losses",
    "csbdeep.internals.nets",
    "csbdeep.internals.predict",
    "csbdeep.internals.probability",
    "csbdeep.internals.train",
    "csbdeep.models",
    "csbdeep.models.base_model",
    "csbdeep.models.care_isotropic",
    "csbdeep.models.care_projection",
    "csbdeep.models.care_standard",
    "csbdeep.models.care_upsampling",
    "csbdeep.models.config",
    "csbdeep.models.pretrained",
    "csbdeep.scripts",
    "csbdeep.scripts.care_predict",
    "csbdeep.utils",
    "csbdeep.utils.plot_utils",
    "csbdeep.utils.six",
    "csbdeep.utils.tf",
    "csbdeep.utils.utils",
    "csbdeep.version",
]

datas = collect_data_files("csbdeep", includes=["**/*"])
