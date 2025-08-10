
import sounddevice as sd

print("Host APIs:", sd.query_hostapis())
print("Default devices (in,out):", sd.default.device)
print("All devices:")
for i, d in enumerate(sd.query_devices()):
    print(i, d["name"], "in:", d["max_input_channels"], "out:", d["max_output_channels"])
