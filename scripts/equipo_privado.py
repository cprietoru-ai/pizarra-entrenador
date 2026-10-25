"""Lo privado de cada equipo conectado, cifrado: fichas nuevas y goleadores.

Uso: python3 -I scripts/equipo_privado.py   (desde la raíz, después de calendario_rfaf.py)
Lo lanza cada día la tarea .github/workflows/calendario.yml. Necesita la
contraseña del equipo en la variable que diga `secreto` en config.json (en
GitHub, un secreto del repo); sin ella no hace nada.

De la web pública de la RFAF lee:
- la lista de jugadores con licencia (NFG_VisEquipos): si aparece alguien que
  no está en la plantilla, lo añade (sin dorsal ni foto, marcado `nuevo`).
  Si alguien desaparece de la lista no se borra: solo se avisa en el log.
- la tabla de goleadores (acumulada) y la de porteros.

Los goleadores de cada partido salen restando la tabla de antes del partido:
cuando sale un resultado nuevo del equipo y la tabla cambia, la diferencia es
de ese partido. Lo que no cuadra con los goles del marcador se apunta como
`propia` (goles en propia puerta del rival, que la RFAF no asigna).

Todo con nombres de jugadores va cifrado (gzip + AES-256-GCM, clave
PBKDF2-SHA256), igual que la plantilla: plantilla.enc.json y
estadisticas.enc.json. En claro solo va la fecha.
"""
import base64, glob, gzip, hashlib, json, os, re, sys, time, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calendario_rfaf import BASE, clean, get  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

ITER = 600_000


def descifrar(ruta, clave):
    r = json.load(open(ruta))
    k = hashlib.pbkdf2_hmac("sha256", clave.encode(), base64.b64decode(r["sal"]), r["iter"], 32)
    return json.loads(gzip.decompress(AESGCM(k).decrypt(base64.b64decode(r["iv"]), base64.b64decode(r["datos"]), None)))


def cifrar(ruta, datos, clave):
    exportado = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    datos = {**datos, "exportado": exportado}
    sal, iv = os.urandom(16), os.urandom(12)
    k = hashlib.pbkdf2_hmac("sha256", clave.encode(), sal, ITER, 32)
    c = AESGCM(k).encrypt(iv, gzip.compress(json.dumps(datos, ensure_ascii=False).encode(), 9), None)
    b = lambda x: base64.b64encode(x).decode()
    json.dump({"app": "pizarra-entrenador", "cifrado": 1, "exportado": exportado, "kdf": "PBKDF2-SHA256",
               "iter": ITER, "sal": b(sal), "iv": b(iv), "datos": b(c)}, open(ruta, "w"))


def norm(s):
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    return frozenset(re.findall(r"[a-z]+", s))


def licencias(cod):
    raw = get(f"{BASE}NFG_VisEquipos?cod_primaria=1000119&Codigo_Equipo={cod}")
    if raw is None or "Jugadores (" not in raw:
        return None
    blk = raw[raw.index("Jugadores ("):]
    blk = blk[:blk.index("</table>")]
    return [clean(x).replace("\xa0", " ").strip() for x in re.findall(r"<td>(.*?)</td>", blk, flags=re.S)]


def tabla(pagina, cfg, yo):
    """{nombre RFAF: (n, goles)} del equipo en NFG_CMP_Goleadores / NFG_CMP_Porteros, o None."""
    for _ in range(4):
        raw = get(f"{BASE}{pagina}?cod_primaria=1000120&codcompeticion={cfg['competicion']}&codgrupo={cfg['grupo']}")
        if raw is None:
            continue
        out = {}
        for fila in re.findall(r"<tr>\s*(<td.*?)</tr>", raw, flags=re.S):
            c = [clean(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", fila, flags=re.S)]
            if len(c) >= 5 and c[3].isdigit() and c[4].isdigit() and c[1].strip() == yo:
                out[c[0]] = (int(c[3]), int(c[4]))
        if out or "Partidos Jugados" in raw or "Minutos" in raw:
            return out
    return None


def slug(nombre_rfaf, usados):
    ap = nombre_rfaf.split(",")[0].split()
    base = unicodedata.normalize("NFD", ap[0] if ap else "jugador").encode("ascii", "ignore").decode().lower()
    s, i = base, 2
    while s in usados:
        s, i = f"{base}-{i}", i + 1
    return s


def bonito(nombre_rfaf):
    ap, _, nom = nombre_rfaf.partition(",")
    return f"{nom.strip().title()} {ap.strip().title()}".strip()


def equipo(carpeta):
    cfg = json.load(open(f"{carpeta}/config.json"))
    clave = os.environ.get(cfg.get("secreto", ""))
    if not clave:
        print(f"{carpeta}: sin la contraseña ({cfg.get('secreto')}), no se toca lo privado")
        return
    if not os.path.exists(f"{carpeta}/plantilla.enc.json"):
        print(f"{carpeta}: aún no hay plantilla cifrada")
        return
    cal = json.load(open(f"{carpeta}/calendario.json"))
    pl = descifrar(f"{carpeta}/plantilla.enc.json", clave)
    jug = pl["jugadores"]
    por_nombre = {norm(j.get("nombre")): s for s, j in jug.items()}
    yo = cfg["nombre_rfaf"]

    # 1. Fichas nuevas
    lic = licencias(cal["cod_equipo"])
    if lic is None:
        print(f"{carpeta}: no se pudo leer la lista de licencias")
    else:
        cambios = False
        for n in lic:
            if norm(n) not in por_nombre:
                s = slug(n, jug)
                jug[s] = {"nombre": bonito(n), "corto": bonito(n).split()[0].upper(), "dorsal": None,
                          "portero": False, "capitan": False, "nuevo": True}
                por_nombre[norm(n)] = s
                cambios = True
                print(f"{carpeta}: ficha nueva ({s})")
        fuera = [s for k, s in por_nombre.items() if k not in {norm(n) for n in lic}]
        if fuera:
            print(f"{carpeta}: {len(fuera)} de la plantilla ya no salen en la lista de licencias (no se borran)")
        if cambios:
            pl.pop("exportado", None)
            cifrar(f"{carpeta}/plantilla.enc.json", pl, clave)

    # 2. Goleadores por partido
    ruta = f"{carpeta}/estadisticas.enc.json"
    est = descifrar(ruta, clave) if os.path.exists(ruta) else {"partidos": {}, "ultima": None}
    gol = tabla("NFG_CMP_Goleadores", cfg, yo)
    por = tabla("NFG_CMP_Porteros", cfg, yo)
    if gol is None:
        print(f"{carpeta}: no se pudo leer la tabla de goleadores")
        return
    sl = lambda n: por_nombre.get(norm(n))
    ahora = {sl(n): g for n, (pj, g) in gol.items() if sl(n)}
    # Semilla de una sola vez (partidos ya jugados antes de esto), con los jugadores como hash
    sem = f"{carpeta}/semilla.json"
    if os.path.exists(sem):
        h = lambda s: hashlib.sha256(("pizarra:" + s).encode()).hexdigest()[:16]
        inv = {h(s): s for s in jug}
        for j, d in json.load(open(sem))["partidos"].items():
            est["partidos"][j] = {"goleadores": {inv[k]: n for k, n in d["goleadores"].items() if k in inv},
                                  "propia": d.get("propia", 0)}
        os.remove(sem)
        print(f"{carpeta}: semilla aplicada")
    antes = est.get("ultima")
    if antes is None:
        # Primera vez: punto de partida. Lo ya jugado que no venga en la semilla
        # queda sin desglose (los goles están en la tabla, pero no se sabe de qué partido)
        est["ultima"] = ahora
        for k, p in cal["partidos"].items():
            if p.get("resultado") and k not in est["partidos"]:
                est["partidos"][k] = {"goleadores": {}, "propia": 0, "sin_desglose": True}
    else:
        pend = [k for k, p in sorted(cal["partidos"].items()) if p.get("resultado") and k not in est["partidos"]]
        for k in list(pend):
            p = cal["partidos"][k]
            mios = int(p["resultado"].split("-")[0 if p["casa"] else 1])
            if mios == 0:
                est["partidos"][k] = {"goleadores": {}, "propia": 0}
                pend.remove(k)
        dif = {s: g - antes.get(s, 0) for s, g in ahora.items() if g - antes.get(s, 0) > 0}
        if dif and len(pend) == 1:
            k = pend[0]; p = cal["partidos"][k]
            mios = int(p["resultado"].split("-")[0 if p["casa"] else 1])
            est["partidos"][k] = {"goleadores": dif, "propia": max(0, mios - sum(dif.values()))}
            est["ultima"] = ahora
            print(f"{carpeta}: goleadores de {k}")
        elif dif and len(pend) > 1:
            # Varios partidos entre dos lecturas: no se puede repartir. Se apuntan sin desglose.
            for k in pend:
                est["partidos"][k] = {"goleadores": {}, "propia": 0, "sin_desglose": True}
            est["ultima"] = ahora
        # Si la tabla cambia sin resultado nuevo, se espera al resultado (no se toca `ultima`)
    est["temporada"] = {sl(n): {"pj": pj, "goles": g} for n, (pj, g) in gol.items() if sl(n)}
    if por is not None:
        est["porteros"] = {sl(n): {"minutos": m, "encajados": g} for n, (m, g) in por.items() if sl(n)}
    viejo = descifrar(ruta, clave) if os.path.exists(ruta) else None
    if viejo is None or {k: v for k, v in viejo.items() if k != "exportado"} != {k: v for k, v in est.items() if k != "exportado"}:
        est.pop("exportado", None)
        cifrar(ruta, est, clave)
        print(f"{carpeta}: estadísticas actualizadas")
    else:
        print(f"{carpeta}: estadísticas sin cambios")


if __name__ == "__main__":
    for c in sorted(glob.glob("equipos/*/config.json")):
        equipo(os.path.dirname(c))
