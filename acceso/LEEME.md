# Acceso con GitHub

El botón **Entrar con GitHub** de Telar necesita dos piezas que se preparan una sola vez: un servidor mínimo en Cloudflare (`worker.js`, gratis) y una app de GitHub llamada «Telar». El servidor guarda el secreto de la app y no ve los nombres que se investigan.

## 1 · El servidor en Cloudflare

1. Crea una cuenta gratuita en [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up).
2. En el menú: **Workers & Pages → Create → Create Worker**. Llámalo `telar-acceso` y pulsa **Deploy**.
3. Pulsa **Edit code**, borra todo, pega el contenido de [`worker.js`](worker.js) y pulsa **Deploy**.
4. Copia la dirección del servidor. Tiene esta forma: `https://telar-acceso.TU-NOMBRE.workers.dev`.

## 2 · La app de GitHub

1. Abre [esta página de GitHub, que ya viene rellenada](https://github.com/settings/apps/new?name=Telar%20investigaciones&url=https%3A%2F%2Fsimonrovi-web.github.io%2Ftelar%2F&public=false&webhook_active=false&issues=write).
2. En **Callback URL** pega la dirección del servidor y agrégale `/vuelta` al final: `https://telar-acceso.TU-NOMBRE.workers.dev/vuelta`.
3. Comprueba lo siguiente:
   - **Webhook · Active** está desmarcado.
   - En **Permissions → Repository**, **Issues** está en *Read and write*.
   - En **Where can this GitHub App be installed?** está marcado **Only on this account**.
4. Pulsa **Create GitHub App**.
5. En la página de la app, copia el **Client ID**. Después pulsa **Generate a new client secret** y copia el secreto: GitHub lo muestra una sola vez.
6. En el menú de la izquierda, entra en **Install App → Install → Only select repositories**, elige **telar-investigador** y pulsa **Install**.

## 3 · Unir las dos piezas

1. En Cloudflare, abre el Worker `telar-acceso` y ve a **Settings → Variables and Secrets → Add**:
   - `GITHUB_CLIENT_ID` (Text): el Client ID.
   - `GITHUB_CLIENT_SECRET` (Secret): el secreto.
2. Pulsa **Deploy**.
3. En `docs/investigador.js`, completa la línea `var ACCESO = { servidor: "", clientId: "" };` con la dirección del servidor y el Client ID. El Client ID no es secreto; el secreto queda solo en Cloudflare.

Por omisión, solo la cuenta `simonrovi-web` puede entrar. Para sumar otra cuenta, agrega en Cloudflare la variable `USUARIOS` con las cuentas separadas por coma.

## Seguridad

- El secreto de la app vive solo en Cloudflare. Nunca va en este repositorio ni en la app.
- El permiso que entrega GitHub dura 8 horas y la app lo renueva sola. Queda solo en el navegador de quien entró, y **Salir** lo borra.
- La app «Telar» solo puede leer y escribir issues de `telar-investigador`: no puede tocar el código ni otros repositorios.
- Para cortar todo el acceso de una vez, desinstala la app en GitHub (**Settings → Applications**).
