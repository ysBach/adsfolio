"""Collect NLTK package resources without collecting a builder's personal corpora."""

from PyInstaller.utils.hooks import collect_data_files

# The spec explicitly includes only the WordNet corpus required by iso4.
datas = collect_data_files("nltk", include_py_files=False)
hiddenimports = ["nltk.chunk.named_entity"]
