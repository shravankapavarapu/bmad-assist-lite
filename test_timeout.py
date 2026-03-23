import subprocess, time

p = subprocess.Popen('python -c "import time; print(123, flush=True); time.sleep(10)"', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

try:
    p.communicate(timeout=1)
except subprocess.TimeoutExpired as e:
    p.kill()
    out, err = p.communicate()
    print('STDOUT HAS:', repr(out))
