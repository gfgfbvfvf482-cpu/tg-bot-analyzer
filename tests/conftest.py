import os
import sys


def _add_project_root_to_sys_path():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, os.pardir))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


_add_project_root_to_sys_path()

