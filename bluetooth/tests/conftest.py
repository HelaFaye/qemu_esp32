import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
if os.environ.get("JCTOOL_PATH"):
    sys.path.insert(0, os.environ["JCTOOL_PATH"])
