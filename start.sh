sudo openvt -c 7 -f -s -- \
  su - cavity -c '
    cd /home/cavity/pda36au-python &&
    xinit /usr/bin/env \
      MPLBACKEND=TkAgg \
      /home/cavity/pda36au-python/.venv/bin/python \
      /home/cavity/pda36au-python/main.py \
      continuous \
      -- :0 vt7 -nolisten tcp
  '