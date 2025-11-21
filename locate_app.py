import sys
import os
print(f"sys.path: {sys.path}")
try:
    import app
    print(f"app file: {app.__file__}")
except Exception as e:
    print(f"Error importing app: {e}")
