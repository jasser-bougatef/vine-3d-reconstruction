#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rt_streamer.py — WebSocket + HTTP server
"""

import argparse
import asyncio
import base64
import json
import sys
import threading
import time
import re
import traceback
import socket
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
FINAL_OUT    = PROJECT_ROOT / "final_Output"
WS_PORT      = 8765
HTTP_PORT    = 8080
CHUNK_SIZE   = 65536  # 64 Ko

_connected: set = set()
_loop: asyncio.AbstractEventLoop = None
_send_queue: asyncio.Queue       = None
_robot_mode = "sim"

DATE_RE = re.compile(r"(\d{2}-\d{2}-\d{4})")

def set_mode(mode: str):
    global _robot_mode
    _robot_mode = mode

async def _ws_handler(websocket):
    global _connected
    addr = websocket.remote_address
    print(f"[WS] ✔ Client connecté : {addr}")
    _connected.add(websocket)
    await _send_one(websocket, {"type": "hello", "message": "Robot RT Pipeline v2.0"})
    try:
        async for raw in websocket:
            try:
                await _handle_incoming(websocket, json.loads(raw))
            except json.JSONDecodeError:
                pass
    except Exception:
        pass
    finally:
        _connected.discard(websocket)
        print(f"[WS] Client déconnecté : {addr}")

async def _handle_incoming(ws, msg: dict):
    try:
        t = msg.get("type", "")
        if t == "ping":
            await _send_one(ws, {"type": "pong"})
        elif t == "get_mode":
            await _send_one(ws, {"type": "mode", "mode": _robot_mode})
        elif t == "sim_ctrl":
            print(f"[WS] 🎮 Commande sim : {msg.get('cmd')}")
        elif t == "request_ply":
            ply = _find_ply(msg.get("pied_id",""), msg.get("date",""))
            if ply:
                asyncio.ensure_future(_stream_ply_to_client(ws, msg["pied_id"], ply, msg.get("date","")))
            else:
                await _send_one(ws, {"type": "alert", "message": "PLY introuvable", "severity": "warn"})
        elif t == "list_pieds":
            pieds_list = _scan_pieds()
            await _send_one(ws, {"type": "pied_list", "pieds": pieds_list})
    except Exception as e:
        print(f"[WS] ❌ Erreur dans _handle_incoming: {e}")

async def _send_one(ws, msg: dict):
    try:
        await ws.send(json.dumps(msg))
    except Exception:
        pass

async def _broadcast(msg: dict):
    global _connected
    if not _connected: return
    data = json.dumps(msg)
    dead = set()
    for ws in list(_connected):
        try:
            await ws.send(data)
        except Exception:
            dead.add(ws)
    for d in dead:
        _connected.discard(d)

async def _queue_dispatcher():
    global _send_queue
    _send_queue = asyncio.Queue()
    while True:
        msg = await _send_queue.get()
        await _broadcast(msg)

def _enqueue(msg: dict):
    global _loop, _send_queue
    if _loop and _send_queue:
        _loop.call_soon_threadsafe(_send_queue.put_nowait, msg)

async def _stream_ply_to_client(ws, pied_id: str, ply_path: Path, date_label: str):
    total   = ply_path.stat().st_size
    n_chunks= (total + CHUNK_SIZE - 1) // CHUNK_SIZE
    print(f"[WS] Streaming {pied_id} → {ws.remote_address} ({total/1024:.1f} Ko)")
    await _send_one(ws, {"type": "ply_start", "pied_id": pied_id, "date": date_label, "total_chunks": n_chunks, "total_size": total})
    with open(ply_path, "rb") as f:
        for idx in range(n_chunks):
            chunk = f.read(CHUNK_SIZE)
            if not chunk: break
            await _send_one(ws, {"type": "ply_chunk", "pied_id": pied_id, "date": date_label, "chunk_idx": idx, "total_chunks": n_chunks, "data": base64.b64encode(chunk).decode()})
            await asyncio.sleep(0.005)
    n_pts = _count_ply_points(ply_path)
    await _send_one(ws, {"type":"ply_done","pied_id":pied_id,"date":date_label,"n_points":n_pts})
    print(f"[WS] ✔ {pied_id} streamé ({n_pts} pts)")

async def _broadcast_ply(pied_id: str, ply_path: Path, date_label: str):
    global _connected
    if not _connected: return
    total   = ply_path.stat().st_size
    n_chunks= (total + CHUNK_SIZE - 1) // CHUNK_SIZE
    await _broadcast({"type":"ply_start","pied_id":pied_id,"date":date_label,"total_chunks":n_chunks,"total_size":total})
    with open(ply_path, "rb") as f:
        for idx in range(n_chunks):
            chunk = f.read(CHUNK_SIZE)
            if not chunk: break
            await _broadcast({"type":"ply_chunk","pied_id":pied_id,"date":date_label,"chunk_idx":idx,"total_chunks":n_chunks,"data":base64.b64encode(chunk).decode()})
            await asyncio.sleep(0.005)
    n_pts = _count_ply_points(ply_path)
    await _broadcast({"type":"ply_done","pied_id":pied_id,"date":date_label,"n_points":n_pts})

def send_status(pied_id, state, n_frames_p1=0, n_frames_p2=0, gps_quality=1):
    _enqueue({"type":"status","pied_id":pied_id,"state":state,"n_frames_p1":n_frames_p1,"n_frames_p2":n_frames_p2,"gps_quality":gps_quality,"ts":time.time()})

def send_gps(x_l93, y_l93, quality, frame, phase="ALLER"):
    _enqueue({"type":"gps_pos","x_l93":round(x_l93,3),"y_l93":round(y_l93,3),"quality":quality,"frame":frame,"phase":phase,"ts":time.time()})

def send_alert(message, severity="warn", sim=False):
    _enqueue({"type":"alert","message":message,"severity":severity,"sim":sim,"ts":time.time()})

def send_preview(pied_id, jpeg_path):
    try:
        with open(jpeg_path,"rb") as f:
            _enqueue({"type":"preview","pied_id":pied_id,"jpeg":base64.b64encode(f.read()).decode()})
    except Exception: pass

async def send_ply_async(pied_id, ply_path, date_label):
    global _loop
    if _loop: asyncio.run_coroutine_threadsafe(_broadcast_ply(pied_id, ply_path, date_label), _loop)

def _count_ply_points(ply_path):
    try:
        with open(ply_path, "rb") as f:
            header_data = f.read(4096).decode("ascii", errors="ignore")
            for line in header_data.split('\n'):
                line = line.strip()
                if line.startswith("element vertex"):
                    return int(line.split()[-1])
                if line == "end_header":
                    break
    except Exception:
        pass
    return 0

def _find_ply(pied_id, date_label):
    for ply in FINAL_OUT.rglob("*.ply"):
        if pied_id in str(ply) and date_label in str(ply): return ply
    return None

def _scan_pieds():
    result = []
    try:
        if not FINAL_OUT.exists():
            print(f"[WS] ⚠️ Le dossier {FINAL_OUT} n'existe pas !")
            return result

        ply_files = list(FINAL_OUT.rglob("*.ply"))
        print(f"[WS] 🔍 Analyse de {len(ply_files)} fichiers .ply trouvés dans final_Output...")

        processed_keys = set()

        for ply in ply_files:
            folder_name = "inconnu"
            for p in ply.parents:
                if p.parent == FINAL_OUT:
                    folder_name = p.name
                    break
            
            if folder_name == "inconnu":
                folder_name = ply.stem

            pied_id = folder_name.split("_")[0]
            match = DATE_RE.search(folder_name)
            date_val = match.group(1) if match else "Date_inconnue"

            unique_key = f"{pied_id}_{date_val}"
            if unique_key in processed_keys: continue
            processed_keys.add(unique_key)

            try:
                rel_path = str(ply.relative_to(PROJECT_ROOT))
            except Exception:
                rel_path = str(ply.name)

            result.append({
                "pied_id":  pied_id,
                "date":     date_val,
                "ply_path": rel_path,
                "n_points": _count_ply_points(ply),
            })
            
        print(f"[WS] ✔ {len(result)} modèles 3D envoyés à l'interface !")
    except Exception as e:
        print(f"[WS] ❌ Erreur critique dans _scan_pieds: {e}")
        traceback.print_exc()
        
    return result

class SilentHTTPHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)
    def log_message(self, fmt, *args): pass
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        super().end_headers()

def _start_http(port):
    server = HTTPServer(("0.0.0.0", port), SilentHTTPHandler)
    print(f"[HTTP] → http://0.0.0.0:{port}  (racine: {PROJECT_ROOT})")
    server.serve_forever()

async def _keepalive():
    global _connected
    while True:
        await asyncio.sleep(30)
        if _connected:
            await _broadcast({"type": "ping"})

async def _main_async(ws_port):
    global _loop, _connected
    
    # Sécurité anti-zombie
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("0.0.0.0", ws_port))
        s.close()
    except OSError:
        print("\n" + "!" * 70)
        print(f" [ERREUR CRITIQUE] LE PORT {ws_port} EST DÉJÀ UTILISÉ !")
        print(" Tapez : killall -9 python3")
        print("!" * 70 + "\n")
        sys.exit(1)
        
    _loop = asyncio.get_running_loop()
    
    try:
        import websockets
    except ImportError:
        print("[ERREUR] Le module 'websockets' n'est pas installé dans le venv.")
        sys.exit(1)
        
    print(f"[WS] → ws://0.0.0.0:{ws_port}")
    try:
        async with websockets.serve(_ws_handler, "0.0.0.0", ws_port):
            await asyncio.gather(_queue_dispatcher(), _keepalive())
    except Exception as e:
        print(f"[WS] Erreur serveur : {e}")
        traceback.print_exc()

def start_streamer(ws_port=WS_PORT, http_port=HTTP_PORT):
    threading.Thread(target=_start_http, args=(http_port,), daemon=True, name="http-srv").start()
    def _ws(): asyncio.run(_main_async(ws_port))
    threading.Thread(target=_ws, daemon=True, name="ws-srv").start()
    for _ in range(30):
        if _loop is not None: break
        time.sleep(0.1)

def main():
    ap = argparse.ArgumentParser(description="WebSocket + HTTP server")
    ap.add_argument("--ws-port",   type=int, default=WS_PORT)
    ap.add_argument("--http-port", type=int, default=HTTP_PORT)
    args = ap.parse_args()
    threading.Thread(target=_start_http, args=(args.http_port,), daemon=True).start()
    print(f"[STREAMER] Démarrage ws://0.0.0.0:{args.ws_port}")
    try: asyncio.run(_main_async(args.ws_port))
    except KeyboardInterrupt: print("\n[STREAMER] Arrêté.")

if __name__ == "__main__":
    main()