import subprocess,time
from pathlib import Path
p=Path('manual_test_files/parallel300_v1')
while not (p/'after/result.json').exists(): time.sleep(10)
time.sleep(15)
subprocess.run(['backend/.venv/Scripts/python',str(p/'recover.py')],check=True)
subprocess.run(['backend/.venv/Scripts/python',str(p/'report.py')],check=True)
