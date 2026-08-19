# Subdominio del Traductor Visual

Objetivo:

| URL | App | Modal 18+ |
|-----|-----|-----------|
| https://remnoirel.com | Catálogo de mods | Sí |
| https://pso2clasic.remnoirel.com | Traductor visual | **No** |
| https://pso2clasic.remnoirel.com/Pso2AnimViewer | PSO2 Animation Viewer | **No** |

El código del traductor **no incluye** modal de edad. Al vivir en **otro subdominio**, no hereda el del catálogo.

> El dominio y el DNS solo los puede configurar el dueño de la cuenta (Railway + Cloudflare/registrador). Este archivo es la checklist.

---

## 1. Railway (servicio del traductor = repo `pso2clasic_`)

1. Abre el servicio del traductor en Railway.
2. **Settings → Networking → Public Networking**.
3. Si aún no hay dominio Railway: **Generate Domain** (deja el `*.up.railway.app` como respaldo).
4. **+ Custom Domain** y escribe exactamente:

   ```text
   pso2clasic.remnoirel.com
   ```

5. Railway te mostrará algo como:
   - **CNAME** → valor tipo `xxxxx.up.railway.app` (o similar)
   - **TXT** → verificación de propiedad (**obligatorio**)

6. Copia ambos valores (los necesitas en el DNS).

---

## 2. Variables de entorno (mismo servicio)

En **Variables** del servicio del traductor, añade o actualiza:

| Variable | Valor |
|----------|--------|
| `PUBLIC_URL` | `https://pso2clasic.remnoirel.com` |
| `CATALOG_URL` | `https://remnoirel.com` (opcional; destino de la llave 🔧) |

Otras que ya debes tener: `DISCORD_TOKEN`, `GITHUB_TOKEN`, etc.

El visor notifica cada descarga a `https://remnoirel.com/api/track_download` para que el contador siga en el **mismo canal de Discord** (`#server-status`). Si el catálogo está caído, el bot del traductor intenta escribir ahí directo (hace falta que esté en el Discord de Rem Noirel).

Tras guardar, Railway redesplegará. Los botones de Discord “Abrir Editor Visual” usarán el subdominio.

---

## 3. DNS (donde gestiones `remnoirel.com`)

Crea **exactamente** lo que Railway indica. Ejemplo típico:

| Tipo | Nombre / Host | Valor / Destino | Proxy |
|------|---------------|-----------------|--------|
| CNAME | `pso2clasic` | el CNAME de Railway | Si usas Cloudflare: al principio **DNS only** (nube gris) hasta que verifique; luego puedes probar proxy |
| TXT | el nombre que diga Railway | el valor TXT de Railway | sin proxy |

Notas Cloudflare:

- SSL/TLS del dominio: modo **Full** (no Full Strict si da problemas con Railway).
- El **TXT** debe existir o Railway no verifica y puede dar 404.
- Propagación: minutos a unas horas (a veces hasta 24–48 h).

---

## 4. Comprobar

1. En Railway, el dominio custom debe mostrar **check verde** (verificado).
2. Abre en el navegador (ventana privada):

   - https://pso2clasic.remnoirel.com/ → buscador del traductor **sin** modal 18+
   - https://pso2clasic.remnoirel.com/Pso2AnimViewer → guía del visor **sin** modal 18+
     - El tornillo 🔧 y **Return to Mod Catalog** llevan a https://remnoirel.com/ (con modal)
   - https://remnoirel.com/ → catálogo **con** modal 18+
   - https://remnoirel.com/Pso2AnimViewer → redirige al visor del subdominio

3. En Discord, un comando que abra el editor debe llevar a  
   `https://pso2clasic.remnoirel.com/edit?...`

---

## 5. Qué no hace falta

- No copiar el HTML del age-gate al traductor.
- No poner el traductor en `remnoirel.com/pso2clasic/` (path) si ya usas subdominio.
- No apuntar el custom domain del **catálogo** al servicio del traductor (cada servicio tiene su dominio).

---

## Resumen de dueños

| Quién | Qué |
|-------|-----|
| **Código** (este repo) | `get_public_url()`, default `pso2clasic.remnoirel.com`, sin modal |
| **Tú en Railway** | Custom Domain + variable `PUBLIC_URL` |
| **Tú en DNS** | CNAME + TXT de `pso2clasic.remnoirel.com` |
