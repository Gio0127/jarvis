# Jarvis — assistente vocale personale

Assistente vocale in italiano che gira in locale sul PC: si sveglia con "Hey Jarvis", risponde a voce con la personalità di un maggiordomo britannico e mostra un HUD olografico con una rete neurale 3D. Può aprire programmi e siti, controllare la musica e Spotify, mandare messaggi WhatsApp e avviare chiamate (sempre con conferma vocale), e avvisa se il PC ha problemi.

Tutto funziona in locale: modello linguistico con Ollama, riconoscimento vocale con faster-whisper, voce con Kokoro. Internet serve solo per il meteo, Spotify e il primo download dei modelli.

---

## 0. Requisiti

| | Minimo | Consigliato |
|---|---|---|
| Sistema | Windows 10/11 | Windows 11 |
| Scheda video | NVIDIA con 8 GB | NVIDIA con 12 GB o più (es. RTX 4070) |
| RAM | 16 GB | 32 GB |
| Spazio su disco | 25 GB liberi | 40 GB liberi |
| Audio | Cuffie con microfono | Cuffie **cablate** con microfono |

Tutti i comandi vanno lanciati in **PowerShell** (tasto Start → scrivi "PowerShell" → Invio). Quando un passaggio dice "riapri PowerShell", chiudi la finestra e aprine una nuova: serve a rendere disponibili i programmi appena installati.

---

## 1. Installare i programmi di base

Copia e incolla un comando alla volta. Se compare una richiesta di autorizzazione, accettala.

```powershell
winget install -e --id Git.Git
winget install -e --id astral-sh.uv
winget install -e --id Ollama.Ollama
```

- **Git** scarica il progetto e tiene traccia delle modifiche.
- **uv** gestisce Python e le librerie, quindi non serve installare Python a parte.
- **Ollama** fa girare il modello linguistico in locale.

La pronuncia italiana della voce usa espeak-ng, che è già incluso nelle librerie: non serve installarlo a parte.

**Riapri PowerShell** e verifica che sia tutto installato:

```powershell
git --version
uv --version
ollama --version
```

Ogni comando deve rispondere con un numero di versione. Se uno dice "non riconosciuto", riapri PowerShell; se ancora non va, reinstalla quel programma.

Imposta il tuo nome per Git (una volta sola):

```powershell
git config --global user.name "Il tuo nome"
git config --global user.email "la-tua-email@esempio.it"
```

---

## 2. Scaricare il modello linguistico

```powershell
ollama pull gemma4:12b
```

Sono circa 8 GB. Per verificare che giri sulla scheda video, fai una domanda di prova:

```powershell
ollama run gemma4:12b --think=false "Ciao, rispondi in una frase"
```

Mentre risponde, in una **seconda** finestra PowerShell lancia:

```powershell
ollama ps
```

Nella colonna PROCESSOR deve comparire **100% GPU**. Se vedi una parte su CPU, il modello è troppo grande per la tua scheda: scegline uno più piccolo (deve supportare gli strumenti, cioè il tool calling) e scrivilo nel campo `modello_llm` delle impostazioni personali (vedi passo 4).

---

## 3. Scaricare il progetto

```powershell
git clone https://github.com/NOME-UTENTE/jarvis.git C:\JarvisVoice
cd C:\JarvisVoice
```

Sostituisci `NOME-UTENTE` con l'utente GitHub del proprietario del repository. Il repository è privato, quindi devi aver accettato l'invito come collaboratore. Al primo `git clone` si aprirà il browser per l'accesso a GitHub.

---

## 4. Impostazioni personali

Ognuno ha le sue impostazioni, che **non vengono condivise** su GitHub.

```powershell
Copy-Item impostazioni_personali.esempio.json impostazioni_personali.json
notepad impostazioni_personali.json
```

Compila i campi e salva:

| Campo | Cosa scrivere |
|---|---|
| `nome` | Il tuo nome: Jarvis ti chiamerà "signore" e userà il nome nel prompt |
| `citta`, `lat`, `lon` | Città e coordinate per il meteo |
| `profilo` | Frasi su di te: lavoro, obiettivi, progetti, hobby |
| `persone` | Frasi sulle persone importanti (es. "Famiglia: la madre Anna, il padre Luca.") |
| `conoscenze` | I tuoi temi, mostrati come nodi nella rete dell'HUD: `sottovoci` sono le etichette, `parole` le parole (o inizi di parola) che accendono il nodo quando le dici |
| `modello_llm` | Il modello di Ollama (passo 2), di solito `gemma4:12b` |
| `voce`, `velocita` | Voce di Kokoro (`im_nicola` maschile, `if_sara` femminile) e velocità (1.0 normale, 1.5 veloce) |
| `spotify_client_id` | Il Client ID di Spotify (passo 8), oppure `""` per non usarlo |

Se il file manca, Jarvis si ferma e ti dice di copiarlo dal modello.

Coordinate della tua città: cerca la città su Google Maps, tasto destro sul punto, e clicca sulle coordinate per copiarle.

---

## 5. Primo avvio

```powershell
cd C:\JarvisVoice
uv run jarvis_voice.py
```

**Il primo avvio è lento** (10–20 minuti, a seconda della connessione): uv scarica Python e tutte le librerie (circa 3–4 GB, tra cui PyTorch e PySide6), poi vengono scaricati i modelli per la parola di attivazione, il riconoscimento vocale e la voce. Dagli avvii successivi parte in pochi secondi.

Cosa deve succedere:

1. Lo schermo si scurisce e parte la sequenza di avvio, con stelle, scie di luce e i nodi che compaiono uno dopo l'altro.
2. Jarvis dice "Buongiorno, signore. Sistemi online." (o buon pomeriggio, o buonasera).
3. La rete si rimpicciolisce e va nell'angolo in alto a destra.
4. Di' **"Hey Jarvis"** (pronunciato all'inglese), aspetta che compaia "Ti ascolto" e fai una domanda.

Al primo avvio viene creato anche `rubrica.json`, con un contatto di esempio. Chiudi Jarvis (vedi sotto) e compila la rubrica:

```powershell
notepad rubrica.json
```

Il formato è `"Nome": "+39 333 1234567"`, un contatto per riga separati da virgole. Togli il contatto di esempio `"Nome"`. Anche questo file **non viene condiviso**.

**Per chiudere Jarvis** premi Ctrl+C nella finestra PowerShell, oppure clic destro sulla sua icona vicino all'orologio (un cerchio azzurro, forse tra le icone nascoste) → **Esci**. L'HUD lascia passare i clic del mouse, quindi non si chiude cliccandoci sopra. Se provi ad avviarlo due volte, la seconda si ferma subito.

---

## 6. Se il microfono non funziona

**Permessi di Windows.** Vai in Impostazioni → Privacy e sicurezza → Microfono e attiva sia "Accesso al microfono" sia "Consenti alle app desktop di accedere al microfono".

**Prova dei microfoni.** Questo script prova tutti i microfoni uno alla volta; quando compare "PARLA ORA", parla per 3 secondi. Alla fine indica quello con il segnale più forte:

```powershell
cd C:\JarvisVoice
uv run test_mic.py
```

Jarvis usa il **microfono predefinito** di Windows: se il migliore non è quello predefinito, impostalo in Impostazioni → Sistema → Audio → Input. Il segnale giusto ha un massimo di almeno qualche centinaio. Se nessuno supera qualche decina:

1. Premi `Win + R`, scrivi `mmsys.cpl` e premi Invio.
2. Vai sulla scheda **Registrazione**, fai doppio clic sul microfono e apri la scheda **Livelli**.
3. Metti Microfono a 100 e Amplificazione a +20 o +30 dB.
4. Mentre parli, la barra verde accanto al microfono deve muoversi.

**Cuffie con un solo jack:** collegale alla presa **frontale** del case, perché quella verde dietro è solo per l'audio in uscita.

**Software audio virtuale** (Voicemeeter, VB-Audio Cable): può "rubare" il microfono. Se hai problemi, chiudilo o disinstallalo.

**Cuffie Bluetooth** (AirPods ecc.): su Windows il loro microfono funziona male con questo programma. Sono consigliate le cuffie cablate o USB.

---

## 7. Messaggi WhatsApp e chiamate (facoltativo)

**WhatsApp Desktop:**

```powershell
winget install -e --id 9NKSQGP7F2NH -s msstore
```

Aprilo e collegalo al telefono inquadrando il codice QR. Poi verifica che Windows apra WhatsApp dai link: sostituisci il numero con il tuo (39 + numero, senza spazi) e lancia

```powershell
Start-Process "whatsapp://send?phone=39XXXXXXXXXX&text=prova"
```

Si deve aprire la chat col testo già scritto (non premere Invio).

**Chiamate normali dal telefono:**

1. Apri **Collegamento al telefono** dal menu Start e associa il telefono. Su iPhone serve l'app "Collegamento a Windows"; su Android, "Collegamento a Windows" o "Link to Windows".
2. Vai in Impostazioni → App → App predefinite → "Scegli le impostazioni predefinite per tipo di collegamento", cerca **TEL** e scegli **Collegamento al telefono**.
3. Prova con

```powershell
Start-Process "tel:+39XXXXXXXXXX"
```

**Per i primi test**, aggiungi te stesso alla rubrica e scrivi a te stesso: "Hey Jarvis, scrivi a [tuo nome] che questa è una prova". Jarvis chiede **sempre** conferma prima di inviare o chiamare.

---

## 8. Spotify (facoltativo, serve Premium)

1. Vai su **developer.spotify.com/dashboard** e accedi con il tuo account Premium.
2. Clicca **Create app** e compila:
   - App name: `Jarvis`
   - Description: `Assistente vocale personale`
   - Redirect URI: `http://127.0.0.1:8888/callback`, **identico**, con `127.0.0.1` e non `localhost`
   - API: spunta **Web API**
3. Salva, apri **Settings** e copia il **Client ID**. Il Client Secret non serve.
4. Incolla il Client ID nel campo Spotify di `impostazioni_personali.json`.
5. Al prossimo avvio si apre il browser: clicca **Accetta**. L'accesso resta salvato in `.spotify_token`, che non viene condiviso.

Prova con "Hey Jarvis, riproduci Albachiara di Vasco Rossi".

---

## 9. Scorciatoia per avviarlo (facoltativo)

Per avviare Jarvis scrivendo solo `jarvis-voce` da qualsiasi cartella:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
if (!(Test-Path $PROFILE)) { New-Item $PROFILE -Force }
notepad $PROFILE
```

Incolla nel file che si apre:

```powershell
function jarvis-voce {
    Push-Location C:\JarvisVoice
    uv run jarvis_voice.py
    Pop-Location
}
```

Salva, chiudi e riapri PowerShell.

**Avvio automatico all'accensione, senza finestre:** premi `Win + R`, scrivi `shell:startup` e premi Invio. Trova dove si trova `uvw.exe` (la versione di uv che non apre finestre):

```powershell
(Get-Command uvw).Source
```

Nella cartella Esecuzione automatica: tasto destro → Nuovo → Collegamento, e come percorso incolla il risultato tra virgolette seguito dal resto del comando, per esempio:

```
"C:\percorso\di\uvw.exe" run --offline pythonw jarvis_voice.py
```

Chiamalo `Jarvis`, poi tasto destro sul collegamento → Proprietà → in **Da** scrivi `C:\JarvisVoice`. Senza finestra, i messaggi finiscono in `jarvis.log` e Jarvis si chiude dall'icona vicino all'orologio. Prima di usarlo, avvia Jarvis almeno una volta normalmente (passo 5): con `--offline` non scarica nulla.

---

## 10. Cosa sa fare Jarvis

| Cosa dire | Cosa fa |
|---|---|
| "Che tempo fa domani?" | Risponde con il meteo e le previsioni |
| "Come sta il mio PC?" | CPU, GPU, RAM, disco e temperatura |
| "Apri Chrome" / "apri i download" | Apre programmi e cartelle |
| "Metti YouTube" / "metti su YouTube …" | Apre siti e ricerche in Chrome |
| "Metti Netflix / Prime Video / DAZN / NOW" | Apre i siti di streaming |
| "Riproduci … su Spotify" | Fa partire brani, artisti, album o playlist |
| "Prossima canzone" / "metti in pausa" | Controlla la musica |
| "Alza il volume" / "volume al 30" | Regola il volume |
| "Scrivi a … che …" | Messaggio WhatsApp, con conferma |
| "Chiama … al telefono / su WhatsApp" | Chiamata, con conferma |
| "Blocca il PC" | Blocca lo schermo |

Dopo ogni risposta Jarvis resta in ascolto per circa 6 secondi: puoi continuare il discorso senza ripetere "Hey Jarvis".

---

## 11. Problemi comuni

| Problema | Soluzione |
|---|---|
| Non reagisce a "Hey Jarvis" | Pronuncialo all'inglese, chiaro. Prova il microfono (passo 6). Puoi abbassare `SOGLIA` nello script (es. 0.3). |
| Si sveglia da solo | Alza `SOGLIA` (es. 0.7). |
| Taglia le frasi a metà | Alza `PAUSA_FINE` nello script (es. 15). |
| "Ollama non raggiungibile" | Avvia Ollama: deve esserci la sua icona vicino all'orologio. |
| Errore "HTTP Error 400" | Il modello scelto non supporta gli strumenti: usa `gemma4:12b`. |
| Risposte molto lente | Controlla con `ollama ps` che il modello giri al 100% sulla GPU. |
| La voce non parte | Controlla in `voce` delle impostazioni il nome della voce (es. `im_nicola`). |
| Avviato senza finestra, non si capisce cosa succede | Leggi `jarvis.log` nella cartella del progetto. |
| La prima risposta dopo una pausa è lenta | Normale: dopo 5 minuti Ollama libera la scheda video e deve ricaricare il modello. |
| Spotify "non collegato" | Controlla Client ID e Redirect URI (passo 8). |
| Messaggio preparato ma non inviato | WhatsApp non era in primo piano: premi Invio a mano. È una protezione voluta. |

---

## 12. Lavorare insieme sul progetto

Prima di iniziare a lavorare, scarica le ultime modifiche:

```powershell
cd C:\JarvisVoice
git pull
```

Per una nuova funzione, crea un ramo separato (es. `promemoria`):

```powershell
git checkout -b promemoria
```

Quando funziona, salva e carica le modifiche:

```powershell
git add .
git commit -m "Aggiunti promemoria e timer"
git push -u origin promemoria
```

Poi su GitHub apri una **Pull Request** per unirla al ramo principale. L'altro la controlla e la approva.

**File che non vengono mai condivisi** (sono esclusi da git): `impostazioni_personali.json`, `rubrica.json`, `.spotify_token`, `CLAUDE.local.md`, `jarvis.log`.

Per aggiungere una libreria usa `uv add nome-libreria`: aggiorna `pyproject.toml` e `uv.lock`, che vanno inclusi nel commit.

Se usate **Claude Code**, aprite la cartella `C:\JarvisVoice`: legge da solo `CLAUDE.md` con le regole e l'architettura del progetto.
