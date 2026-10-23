"""Calendario de cada equipo conectado, desde la web pública de la RFAF.

Uso: python3 -I scripts/calendario_rfaf.py   (desde la raíz del repo)
Lee equipos/<código>/config.json y escribe equipos/<código>/calendario.json
con las jornadas del equipo: rival, casa/fuera, fecha, hora, campo, escudo y
resultado. Los escudos se bajan una vez a equipos/<código>/escudos/.
Lo lanza cada día la tarea .github/workflows/calendario.yml.

La web de la RFAF pide entrar dos veces con la misma cookie, va en latin-1 y
a veces devuelve la página vacía: por eso los reintentos.
Solo datos públicos (calendario y resultados). Nunca jugadores: esos van
cifrados en plantilla.enc.json.
"""
import glob, html, http.cookiejar, io, json, os, re, sys, time, urllib.request
from PIL import Image

BASE = "https://www.rfaf.es/pnfg/NPcd/"
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
op.addheaders = [("User-Agent", "Mozilla/5.0")]


def get(url):
    for i in range(6):
        try:
            op.open(url, timeout=30).read()
            raw = op.open(url, timeout=30).read().decode("latin-1")
            if len(raw) > 5000 and "No se ha aceptado el cookie" not in raw:
                return raw
        except Exception as e:
            sys.stderr.write(f"reintento {i}: {e}\n")
        time.sleep(2 + i * 2)
    raise SystemExit(f"La RFAF no devuelve la página: {url}")


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def jornada(grupo, j):
    raw = get(f"{BASE}NFG_CmpJornada?cod_primaria=1000120&CodGrupo={grupo}&CodJornada={j}")
    out = []
    for b in raw.split('<tr style="background-color: #ffffff00;">')[1:]:
        teams = re.findall(r'Codigo_Equipo=(\d+)">\s*(.*?)\s*</a>', b, flags=re.S)
        if len(teams) < 2:
            continue
        esc = re.findall(r'src="(https://rfaf\.filesnovanet\.es/[^"]+)"', b)
        hor = [clean(x) for x in re.findall(r"<span class=horario[^>]*>(.*?)</span>", b, flags=re.S)]
        campo = re.search(r"NFG_VisCampos[^>]*>(.*?)</a>", b, flags=re.S)
        out.append({"local": clean(teams[0][1]), "visitante": clean(teams[1][1]),
                    "cod_local": teams[0][0], "cod_visitante": teams[1][0],
                    "esc_local": esc[0] if esc else None, "esc_visitante": esc[1] if len(esc) > 1 else None,
                    "fecha": " ".join(hor), "campo": clean(campo.group(1)) if campo else ""})
    return out


def resultados(grupo, cod):
    raw = get(f"{BASE}NFG_VisCompeticiones_Grupo?&cod_primaria=1000123&codequipo={cod}&codgrupo={grupo}")
    pat = r"<h5>(\d+)</h5></td>\s*<td[^>]*>\s*<h5>(.*?)</h5>\s*<h5>(.*?)</h5>.*?</td>\s*<td[^>]*>\s*<h5>(.*?)</h5>"
    res = {}
    for j, l, v, r in re.findall(pat, raw, flags=re.S):
        m = re.search(r"<b>\s*(\d+)\s*</b>\s*-\s*<b>\s*(\d+)\s*</b>", r)
        if m:
            res[int(j)] = f"{m[1]}-{m[2]}"
    return res


def nombre(n):
    n = re.sub(r"^(C\.D\.|CLUB DEPORTIVO|CLUB DE FUTBOL|A\.D\.F\.)\s+", "", n).replace('"', "").title()
    for a, b in (("Balompie", "Balompié"), ("Atletico", "Atlético"), ("Almuñecar", "Almuñécar"),
                 (" De ", " de "), ("Cajar", "Cájar"), ("Alborán 2021", "Alborán")):
        n = n.replace(a, b)
    return n


def escudo(carpeta, cod, url):
    ruta = f"{carpeta}/escudos/{cod}.png"
    if not os.path.exists(ruta) and url:
        req = urllib.request.Request(url, headers={"Referer": "https://www.rfaf.es/", "User-Agent": "Mozilla/5.0"})
        im = Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=30).read()))
        im.thumbnail((140, 140))
        im.save(ruta, optimize=True)
    return ruta if os.path.exists(ruta) else ""


def equipo(carpeta):
    cfg = json.load(open(f"{carpeta}/config.json"))
    grupo, yo = cfg["grupo"], cfg["nombre_rfaf"]
    partidos, cod, res = {}, None, {}
    for j in range(1, 40):
        js = jornada(grupo, j)
        if not js:
            break
        p = next((p for p in js if yo in (p["local"], p["visitante"])), None)
        if not p:
            continue
        casa = p["local"] == yo
        if cod is None:
            cod = p["cod_local"] if casa else p["cod_visitante"]
            res = resultados(grupo, cod)
        rival = p["visitante"] if casa else p["local"]
        m = re.match(r"(\d{2})-(\d{2})-(\d{4})(?: (\d{2}:\d{2}))?", p["fecha"])
        partidos[f"j{j:02d}"] = {
            "jornada": j, "rival": nombre(rival), "casa": casa,
            "fecha": f"{m[3]}-{m[2]}-{m[1]}" if m else "", "hora": (m[4] or "") if m else "",
            "campo": p["campo"], "resultado": res.get(j, ""),
            "derbi": any(d in rival for d in cfg.get("derbis", [])),
            "escudo_rival": escudo(carpeta, p["cod_visitante"] if casa else p["cod_local"],
                                   p["esc_visitante"] if casa else p["esc_local"])}
    if not partidos:
        raise SystemExit(f"{carpeta}: no sale ningún partido (¿cambió la web?)")
    nuevo = {"club": cfg["club"], "categoria": cfg["categoria"], "escudo": cfg["escudo"], "partidos": partidos}
    ruta = f"{carpeta}/calendario.json"
    viejo = json.load(open(ruta)) if os.path.exists(ruta) else {}
    if {k: v for k, v in viejo.items() if k != "actualizado"} == nuevo:
        print(f"{carpeta}: sin cambios")
        return
    nuevo["actualizado"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    json.dump(nuevo, open(ruta, "w"), ensure_ascii=False, indent=1)
    print(f"{carpeta}: {len(partidos)} partidos")


if __name__ == "__main__":
    for c in sorted(glob.glob("equipos/*/config.json")):
        equipo(os.path.dirname(c))
