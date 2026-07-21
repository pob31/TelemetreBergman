"""Ad-hoc OSC sender for live-validating Millumin custom address bindings.

    python scripts/osc_test.py /front/scale/1 0.25 0.5 0.85 0.5
    python scripts/osc_test.py /front/positionV/1 0 120 240 120
"""
import sys
import time

from pythonosc.udp_client import SimpleUDPClient

HOST, PORT = "127.0.0.1", 5000

addr = sys.argv[1] if len(sys.argv) > 1 else "/front/scale/1"
vals = [float(x) for x in sys.argv[2:]] or [0.25, 0.5, 0.85, 0.5]

client = SimpleUDPClient(HOST, PORT)
for v in vals:
    client.send_message(addr, v)
    print(f"envoye {addr} = {v}", flush=True)
    time.sleep(1.5)
print("FIN")
