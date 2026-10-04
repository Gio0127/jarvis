"""Prova tutti i microfoni uno alla volta: parla quando compare PARLA ORA. Alla fine indica il migliore."""
import numpy as np
import sounddevice as sd

# Un solo host API (quello del microfono predefinito, lo stesso che usa Jarvis): niente doppioni
host = sd.default.hostapi
predefinito = sd.query_hostapis(host)["default_input_device"]
candidati = [i for i, d in enumerate(sd.query_devices())  # il "Mapper" di Windows e' solo un alias del predefinito
             if d["hostapi"] == host and d["max_input_channels"] > 0 and "mapp" not in d["name"].lower()]
print(f"Host API: {sd.query_hostapis(host)['name']} - {len(candidati)} microfoni da provare")

risultati = {}
for dev in candidati:
    info = sd.query_devices(dev)
    sr = int(info["default_samplerate"])
    try:
        with sd.InputStream(device=dev, samplerate=sr, channels=1, dtype="int16") as s:
            print(f"\n[{dev}] {info['name']} - PARLA ORA (3 secondi)...")
            livelli = [int(np.abs(s.read(sr // 10)[0]).mean()) for _ in range(30)]
            print(f"   volume medio {sum(livelli) // len(livelli)}, massimo {max(livelli)}")
            risultati[dev] = max(livelli)
    except Exception as e:
        print(f"\n[{dev}] {info['name']} - errore: {e}")

if risultati:
    migliore = max(risultati, key=risultati.get)
    print(f"\nSegnale più forte: [{migliore}] {sd.query_devices(migliore)['name']} (massimo {risultati[migliore]})")
    if migliore != predefinito:
        print("Non è il microfono predefinito: impostalo come predefinito in Windows "
              "(Impostazioni > Sistema > Audio > Input), perché Jarvis usa quello.")
else:
    print("\nNessun microfono ha funzionato: vedi il passo 6 del README.")
