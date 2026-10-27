# Mini ERP Contable

Sistema contable web que implementa la **Etapa 3** del enunciado: *plan de
cuentas, operaciones, estructura del Libro Diario, estructura del Libro Mayor,
balance y reglas de validación*.

La idea central del sistema es una sola:

```
Plan de cuentas  ──►  Libro Diario  ──►  Libro Mayor  ──►  Balance
   (sección 13)        (sección 14)     (sección 15)     (sección 16)
   (se carga)          (se carga)       (se calcula)     (se calcula)
```

El Libro Mayor y el Balance **no tienen tablas propias**: son consultas sobre
el Libro Diario. Por eso no se pueden cargar "a mano" y siempre reflejan
exactamente lo que se contabilizó (controles 4 y 5 del enunciado).

> Versión propia basada en el proyecto **Mini-ERP-PiraPires** original, con
> estas mejoras:
>
> 1. **Borrador → asientos.** Si le pasás a Pira Pires un borrador de
>    operaciones (en el chat o en un archivo adjunto), arma los asientos en el
>    formato CSV del importador, usando solo cuentas de detalle activas de tu
>    plan de cuentas.
> 2. **«Revisar e importar al Libro Diario».** Los asientos propuestos
>    aparecen como tabla debajo de la respuesta. El botón abre una vista
>    previa con la validación completa (cuadre, cuentas existentes, fechas) y
>    recién al confirmar se guardan.
> 3. **Excel (.xlsx) además de CSV.** Se pueden importar plan de cuentas y
>    asientos desde Excel, descargar las plantillas en Excel y exportar el
>    plan de cuentas y el Libro Diario a CSV o Excel. Todo sigue funcionando
>    sin dependencias externas: el .xlsx se lee y se escribe con la
>    biblioteca estándar (`erp/excel.py`).

---

## Cómo se usa

No hay nada que instalar: el sistema usa solo la **biblioteca estándar de
Python 3** y **SQLite**, que ya vienen con Python.

```bash
./INICIAR.sh                        # lo más simple: carga la demo y abre el navegador
./INICIAR.sh 9000                   # en otro puerto
./INICIAR.sh 8000 limpio            # vacía la base antes de cargar

python3 main.py                    # arranca en http://127.0.0.1:8000
python3 main.py --cargar-demo      # carga la empresa y las 15 operaciones, y sale
python3 main.py --puerto 9000      # otro puerto
python3 main.py --reiniciar        # vacía la base de datos
python3 main.py --abrir            # abre el navegador automáticamente
```

Luego, en la web: **Panel → «Cargar datos de prueba»** y recorrer el menú
`Plan de cuentas → Libro Diario → Libro Mayor → Balance`.

### El acceso

Al abrir la aplicación se elige cómo usarla:

- **Empresa**: se elige el tipo (las 15 figuras jurídicas de Paraguay: empresa
  unipersonal, S.A., S.R.L., E.A.S., sociedad colectiva, comandita simple y por
  acciones, capital e industria, accidental, cooperativa, asociación, fundación,
  sucursal de sociedad extranjera, consorcio/UTE y economía mixta), se completan
  los datos de la empresa y los propios de la figura, y se define el usuario y
  la contraseña. La cuenta queda guardada en el navegador (`localStorage`) con
  la contraseña como huella SHA-256; al entrar, los datos de la empresa se
  copian a la tabla `empresa`.
- **Uso individual**: usuario `Franco` y contraseña `Franco`.

Es una pantalla de demostración: no hay autenticación en el servidor y la
sesión vive en `sessionStorage` (se cierra con «Salir» o al cerrar la pestaña).
La empresa es única por instalación: si se crea otra cuenta, reemplaza los
datos de la empresa. En el Panel se muestra el **perfil cargado en el acceso**,
y los cambios que se hagan en «Datos de la empresa» también se guardan en la
cuenta del navegador.

### Importar tus datos desde un CSV

Además de la carga manual y de los datos de prueba, el plan de cuentas y el
Libro Diario se pueden cargar desde un archivo **CSV**. Los botones están en
**Panel → «Cargar tus propios datos»** y también en las cabeceras de **Plan de
cuentas** y **Libro Diario**. Las plantillas descargables viven en
`web/plantillas/`:

- `web/plantillas/cuentas.csv` — plan de cuentas de ejemplo.
- `web/plantillas/asientos.csv` — dos asientos de ejemplo, ya cuadrados.

**Plan de cuentas.** Columnas:

```csv
codigo;nombre;clasificacion;naturaleza;tipo;estado
1;ACTIVO;1;;agrupadora;ACTIVA
1.1.01;Caja;1;D;detalle;ACTIVA
```

- `codigo` y `nombre` son obligatorios.
- `clasificacion` acepta `1` a `6` o el nombre (`ACTIVO`, `PASIVO`,
  `PATRIMONIO NETO`, `INGRESOS`, `COSTOS`, `GASTOS`). Si se deja vacía, se
  deduce del primer dígito del código.
- `naturaleza` acepta `D`/`A` (o «deudora»/«acreedora»). Si se deja vacía, se
  deduce de la clasificación; la Depreciación Acumulada es el caso especial de
  activo con naturaleza acreedora.
- `tipo` acepta `detalle` o `agrupadora`. Si se deja vacío, las cuentas de
  primer nivel y las que tienen subcuentas quedan como agrupadoras.
- `estado` acepta `ACTIVA` o `INACTIVA`; si se deja vacío, es `ACTIVA`.
- Las cuentas que ya existen **se omiten**; no se actualizan desde el CSV.

**Libro Diario.** Columnas:

```csv
asiento;fecha;concepto;cuenta;detalle;debe;haber
1;2026-01-10;Venta al contado;1.1.01;Cobro;1.100.000,00;
1;2026-01-10;Venta al contado;4.1.01;Venta gravada;;1.000.000,00
1;2026-01-10;Venta al contado;2.1.02;IVA débito fiscal;;100.000,00
```

- `asiento` agrupa las líneas. Si no se incluye esa columna, se agrupan las
  filas consecutivas que comparten la misma fecha y concepto.
- `fecha` acepta `AAAA-MM-DD` o `DD/MM/AAAA`. Los importes usan el formato
  paraguayo (`1.100.000,00`). Si la planilla usa coma decimal, conviene guardar
  el CSV con punto y coma (`;`) como separador, que es lo que hace Excel en
  español.
- Cada asiento debe tener al menos dos líneas, cuentas existentes, activas y de
  detalle, y el total Debe debe ser igual al total Haber. Se importa
  directamente como **CONFIRMADO**.
- El importador valida todo primero y muestra una vista previa con el resumen.
  Si una sola fila falla, **no se guarda nada** y se indica el número de fila
  del archivo para corregirla.

El separador puede ser coma, punto y coma, tabulador o barra vertical: se
detecta solo. El archivo se lee como UTF-8 y el máximo es **1,5 MB**. Recordá
que el orden importa: primero las cuentas y después los asientos.

### La interfaz

Está hecha con **Tailwind CSS v4**, el mismo motor que usa 21st.dev, cargado
desde `web/vendor/` para que funcione aunque no haya internet. No hace falta
compilar nada: el `<style type="text/tailwindcss">` de `web/index.html`
acepta `@theme` y `@apply`, así que se puede experimentar cambiando el tema o
las clases de componente directamente ahí.

El diseño sigue el estilo de **TailGrids UI v3** en su estructura (componentes,
radios, sombras, tipografía), pero la **paleta de colores** es la del tema
**«WhatsApp» de 21st.dev** (`@serafimcloud`): un verde vivo con su escala
completa. Los valores salen de la imagen de presentación del tema.

| Concepto | Valor del tema WhatsApp | Dónde se usa acá |
|----------|--------------------------|------------------|
| Primary | `#25D366` | `--color-primary-500`: realces y acentos decorativos |
| Primary accesible | `#118442` | `--color-primary-600`: botones y texto sobre blanco (AA, 4.77:1) |
| Primary Dark | `#075E54` | `--color-primary-700`: estado hover y encabezados sólidos |
| Primary Light | `#E7F8F0` | `--color-primary-50`: realce de filas, ítems de navegación, badges |
| Verde profundo | `#053B35` | `--color-primary-900`: texto de máximo contraste |
| Lienzo | `#FFFFFF` / `#F0F2F5` | tarjetas blancas sobre página `--color-surface-50` |
| Escala de grises | `#F0F2F5` … `#09090B` | alias `--color-surface-*` |
| Negro de marca | `#09090B` | `--color-tinta`; es el que tiñe todas las sombras |
| Semánticos | success, warning, danger, info | badges, alertas y avisos flotantes |
| Radio | 8px en botones, tarjetas y campos; 12px en diálogos | `--radius-card`, `--radius-field`, `--radius-modal` |
| Sombras | sm en tarjetas, md en desplegables, lg en popovers, 2xl en modales | `--shadow-xs` … `--shadow-2xl` |
| Tipografía | Inter 14px / 1.60 / peso 400 | cuerpo; se pide por CDN y cae a `system-ui` sin internet |
| Foco | anillo de 3px de verde al 25% | `:focus-visible` en la capa base |

El verde vivo `#25D366` solo alcanza 1.98:1 contra blanco, así que no se usa
para texto: queda reservado a lo decorativo. Donde hace falta legibilidad se
usa `#118442`, que es el mismo matiz (H=142) oscurecido hasta cumplir AA.

El resto de las decisiones —un solo botón primario lleno por vista, el
acento izquierdo de 4px en las alertas, el encabezado de tabla en 12px
mayúsculas sobre gris 50, `opacity: 40%` para deshabilitado— salen de la
misma guía.

Como en esta máquina no hay Node ni npm, los componentes de TailGrids
—que son de React e instalan con `npx @tailgrids/cli`— no se pueden instalar, así
que `web/ui.js` reproduce ese contrato a mano sobre Tailwind v4:

| Componente | Props implementadas | Qué aporta |
|------------|--------------------|------------|
| **Button** | `variant` · `appearance` · `size` · `iconOnly` · `pending` · `disabled` · `readOnly` · `onPress` | `variant` y `appearance` se combinan libres (`danger` + `outline`), y `pending` atenúa el contenido marcando `aria-busy` **sin quitar el foco** |
| **Badge** | `variant` · `appearance` (`light`/`solid`) · `iconName` | Estados de los asientos y de los controles |
| **Field** | `label` · `description` · `errorMessage` · `isRequired` | Agrupa etiqueta, control, ayuda y error, con el `id` ya enlazado por `for` |
| **Dialog** | `titulo` · `descripcion` · `contenido` · `campo` · `onConfirmar` | Modal con foco atrapado (Tab y Shift+Tab circulan dentro), cierre con `Esc`, scroll bloqueado y el foco devuelto al elemento que lo abrió. `contenido` permite mostrar HTML ya escapado (por ejemplo, la vista previa de una importación). Reemplaza a `confirm()` y `prompt()`, que son bloqueantes y no se pueden leer con lector de pantalla |
| **Toast** | `variant` · `title` · `description` · `detalles` | Avisos en una región `aria-live="polite"`: se anuncian sin robar el foco |
| **Tabs** | navegación con teclado | Los dos menús (lateral y móvil) controlan los mismos paneles: flechas y `Home`/`End` recorren las pestañas, con *roving tabindex* para que el Tab no haga seis paradas |

Los modales, los toasts y las confirmaciones antes usaban `alert()`, `confirm()`
y `prompt()` del navegador: quedaban como diálogos del sistema, sin estilo y sin
respetar el diseño de la aplicación.

| Vista | Qué muestra |
|-------|-------------|
| **Panel** | Indicadores del período, estado de los 6 controles, datos de la empresa, carga de datos propios desde CSV y datos de prueba |
| **Plan de cuentas** | Alta / edición / baja de cuentas, con validación del código y de la clasificación |
| **Libro Diario** | Alta de asientos con partidas dinámicas, cuadre en vivo, ciclo de vida completo |
| **Libro Mayor** | Movimiento de una cuenta o de todas, con saldo acumulado en cada línea |
| **Balance** | Balance de comprobación con subtotales por clasificación y verificaciones |
| **IVA** | Calculadora del impuesto, generación de asientos de venta y compra, y liquidación mensual del período |
| **Controles** | Las 6 reglas de la sección 18 y la demostración de la trazabilidad |

### Pira Pires, el asistente

**Pira Pires** es el asistente contable con IA (Groq como motor principal y
Gemini como respaldo automático). Responde **solo** con normativa de Paraguay
—leyes, SET, DNIT, SITRADE— y rechaza con educación las consultas sobre
normativa de otros países, redirigiendo a la paraguaya.

Se abre de dos maneras, con la misma conversación compartida:

* desde la pestaña **Asistente** del menú lateral, o
* desde la **burbuja flotante** de la esquina inferior derecha, que despliega
  un panel de chat sobre la vista en la que se está trabajando.

La burbuja es un círculo de **72 px** y se **puede arrastrar** a cualquier
lugar de la pantalla, como la de Messenger: se agarra con el mouse o el dedo y
queda donde se suelta, siempre dentro de la ventana. La posición se recuerda
entre visitas. Para devolverla a su esquina alcanza con hacerle **doble clic**
(o **mantener presionado** en el celular). Un toque sin movimiento abre o
cierra el chat; un arrastre, no: los dos gestos se distinguen por cuánto se
movió el puntero.

Cuando Pira Pires termina de responder con el panel cerrado, aparece un
**puntito de aviso** sobre la burbuja, como el de Messenger; se apaga solo al
abrir el chat. Al pasar el mouse por la burbuja aparece además un botón de
**basura** que la quita de la pantalla (en el celular se ve siempre). Antes de
quitarla pide confirmación y el aviso que aparece trae un **«Deshacer»**.

**Pira Pires pregunta antes de calcular sueldos.** Cuando la consulta es sobre
salarios, aguinaldo o liquidación de empleados, no tira el neto de una: primero
pregunta si corresponde el descuento del **IPS** (aporte obrero del 9 %), si hay
otros descuentos (anticipos, préstamos, embargos), si entran aguinaldo u horas
extras y si hay que registrar el aporte patronal. Recién con esas respuestas
hace el cálculo renglón por renglón.

La foto del asistente es un pez: **boca cerrada** cuando escucha y **boca
abierta** mientras redacta. Las respuestas no aparecen de golpe: se **escriben
palabra por palabra** con un cursor que parpadea, al ritmo del largo del texto.
El pez mantiene la boca abierta mientras escribe y la cierra al terminar.

**Pira Pires recibe archivos.** Desde el clip del pie del chat se puede adjuntar:

* **imágenes** (capturas de pantalla, fotos de libros o balances) → se mandan
  al modelo con visión;
* **CSV, TXT, JSON, MD y TSV** → se leen como texto y se agregan al mensaje;
* **Excel (.xlsx)** → se convierte la primera hoja a CSV y se agrega al
  mensaje;
* **PDF y .xls viejos** → se aceptan, pero como no hay extractor de texto
  en la biblioteca estándar se le avisa al modelo que no se pudieron leer y se
  recomienda pasarlos como imagen o CSV.

Los adjuntos quedan en una tira de etiquetas sobre el pie, con miniatura en el
caso de las imágenes y una **×** para quitarlos. Se envían una sola vez, junto
con la próxima pregunta (que puede ir vacía: adjuntar un balance y dejar que
Pira Pires lo mire es un caso válido). El tope por archivo es de **8 MB** y el
texto de cada uno se recorta a **100 000 caracteres** para no arrastrar un CSV
gigante en cada consulta.


---

## Estructura del proyecto

```
main.py                   punto de entrada (servidor web / consola)
erp/
  dinero.py               importes en centavos enteros + formato español
  excepciones.py          errores que saben qué regla (C1..C6) se infringió
  base_datos.py            esquema SQLite y conexión
  validaciones.py          núcleo de reglas de negocio (controles 1 a 6)
  plan_de_cuentas.py       Etapa 1 · Plan de cuentas
  libro_diario.py          Etapa 1 · Libro Diario (única fuente de carga)
  libro_mayor.py           Etapa 2 · Libro Mayor automático
  balance.py               Etapa 3 · Balance de comprobación
  iva.py                   IVA de Paraguay (Ley N.º 1259/97)
  asistente.py             Pira Pires: Groq con respaldo en Gemini
  datos_demo.py            empresa ficticia con las 15 operaciones
  servidor.py              API JSON + servidor de archivos estáticos
web/
  index.html               interfaz, tokens @theme y clases de componente
  ui.js                    componentes con la API de props de TailGrids
  app.js                   lógica de la interfaz
  img/                     fotos de Pira Pires (boca abierta y cerrada)
  vendor/                  Tailwind v4 vendorizado
datos/                     aquí se crea erp.db al arrancar
INICIAR.sh                arranque rápido (carga la demo y abre el navegador)
render.yaml               definición del servicio para Render
requirements.txt          vacío a propósito: no hay dependencias externas
```

## Publicarlo en Render

El proyecto es Python 3 con SQLite, así que el hosting tiene que poder
ejecutar un proceso Python. Render lo hace con un "Blueprint": la
configuración completa está en `render.yaml`, versionada en el repo.

### 1. Subir el código a GitHub

Render despliega desde un repositorio git, así que primero hay que publicarlo:

```bash
git init
git add .
git commit -m "Mini ERP Contable"
# Crear un repositorio vacío en github.com y luego:
git remote add origin https://github.com/TU_USUARIO/erp-contable.git
git branch -M main
git push -u origin main
```

En este repositorio ya está hecho: el remoto se llama `origen` y apunta a
`https://github.com/FrancoMartinez33/Mini-ERP-PiraPires.git`.

```bash
git push origen main     # subir los cambios nuevos
```

### 2. Crear el servicio

En el panel de Render: **New → Blueprint**, apuntar al repositorio y Render
lee `render.yaml`. La región es `ohio` y **no se puede cambiar después**;
se eligió la más cercana a Paraguay.

Queda un `startCommand` de una línea:

```
python3 main.py
```

`main.py` lee `HOST` y `PORT` del entorno, que es como Render indica que
tiene que escuchar un servicio. El servidor **arranca sin ningún dato
cargado**: sin asientos, sin plan de cuentas y con los datos de la empresa
en blanco. Si se quiere ver la demostración, se carga desde el Panel con
«Cargar datos de prueba» (pide confirmación porque reemplaza las cuentas
existentes).

> **Ojo con `HOST`.** Render solo inyecta `PORT` (10000 por defecto) y
> exige que el servidor escuche en `0.0.0.0`, no en `127.0.0.1`: si escucha
> solo en el bucle local, el proxy de Render no lo alcanza y todas las
> páginas devuelven error 502. Por eso `render.yaml` fija `HOST=0.0.0.0`
> y `main.py` además lo deduce solo cuando detecta la variable `RENDER`.

### 3. Las claves del asistente (opcional)

`claves.env` está ignorado por git, así que **no sube al repositorio**. Las
claves hay que ponerlas en el panel:

- **Render → Environment → tu servicio → Environment Variables**: agregar
  `GROQ_API_KEY` y/o `GEMINI_API_KEY`. (El Blueprint ya las declara con
  `sync: false`, así que Render las pide al crearlo; también se pueden
  agregar después a mano.)
- **Local**: seguir usando `claves.env` en la carpeta del proyecto.

Si no hay ninguna clave, la aplicación funciona igual y solo el chat con IA
queda deshabilitado.

### Qué esperar del plan gratuito

`plan: free` no pide tarjeta, pero tiene tres límites que conviene conocer
antes de mirar la URL:

| Límite | Qué significa acá |
|--------|-------------------|
| Disco efímero | No admite disco persistente. La base se regenera en cada arranque. |
| Se apaga a los 15 min | Y **al apagarse también se borra el disco**. Los asientos que cargues se pierden. |
| 750 horas por mes | Suficiente para una demostración. |

Por eso el arranque **no siembra nada**: cada vez que el servicio se
reinicia, la aplicación vuelve a quedar vacía (empresa, cuentas y asientos
en blanco), lista para cargar un ejercicio desde cero. Para que los datos
sobrevivan de verdad hace falta un plan pago con disco persistente; en ese
caso conviene agregar `--auto-sembrar` al `startCommand` para que la demo
se cargue sola cuando la base está vacía.

### Decisiones de diseño

**Importes en enteros de centavos.** Nunca se usa `float` para dinero. Todos
los importes se guardan y se comparan como enteros (`18965000000` =
G. 189.650.000,00) y solo se convierten a decimal al presentarlos. Así el
control 1 nunca falla por error de redondeo binario.

**Solo cuatro tablas:** `empresa`, `cuenta`, `asiento`, `partida`.

**Ciclo de vida del asiento:** `BORRADOR → CONFIRMADO → ANULADO`.

- Un asiento `BORRADOR` se puede editar y borrar.
- `CONFIRMADO` **no se puede editar ni borrar**: se anula, y queda registrado
  con su motivo para no perder la trazabilidad.
- Solo los asientos `CONFIRMADO` suman en el Libro Mayor y en el Balance.

**Dos tipos de problemas al guardar un asiento.** No todos los errores son
iguales, y el sistema los distingue:

| Tipo | Ejemplo | Qué hace el sistema |
|------|---------|---------------------|
| Estructural (no se puede guardar) | Una línea con Debe y Haber a la vez; una línea vacía; una cuenta inexistente | Rechaza la operación e indica la línea exacta |
| De cuadre (se puede guardar) | Total Debe ≠ total Haber; cuenta inactiva; cuenta agrupadora | Guarda el asiento como `BORRADOR` y explica por qué no se contabilizó |

**Naturaleza por defecto según la clasificación, pero ajustable.**
Activo, Costos y Gastos son deudores; Pasivo, Patrimonio e Ingresos son
acreedores. La cuenta `1.2.02 Depreciación Acumulada` rompe la regla a
propósito: es un Activo pero se contabiliza con saldo acreedor, porque es una
cuenta contrapasiva. Es el mejor ejemplo para mostrar que el ERP de verdad
distingue las dos naturalezas.

**El IVA se discrimina, no se resta.** Cuando el importe ya trae el impuesto
adentro (lo habitual en una factura), la base se obtiene dividiendo por
`1 + alícuota`, no restando un porcentaje del total. Restar el 10 % de un total
de G. 1.100.000 daría G. 110.000 de IVA, cuando el IVA real es G. 100.000. Con
la división, la base reconstruida siempre vuelve a dar el total exacto y el
asiento nunca queda descuadrado por un centavo.

**Cuentas agrupadoras** (`recibe_movimiento = 0`, como `1 ACTIVO` o
`2.1.01 Proveedores`… bueno, las de nivel 1 y 2) no admiten asientos: el
movimiento se registra siempre en una cuenta de detalle.

---

## Los controles de la sección 18

| Control | Regla | Cómo lo implementa el sistema |
|---------|-------|-------------------------------|
| **C1** | Cada asiento debe cuadrar: Debe = Haber | Se valida antes de contabilizar. Un descuadre nunca llega a `CONFIRMADO`. Además hay `CHECK` en la base de datos |
| **C2** | Solo cuentas existentes, activas y de detalle | Validación al crear el asiento + clave foránea en SQLite |
| **C3** | Las cuentas deben estar clasificadas correctamente | El código es jerárquico y su primer dígito tiene que coincidir con la clasificación; las cuentas de nivel 2 y 3 exigen que exista la cuenta padre |
| **C4** | El Libro Mayor se genera del Libro Diario | `libro_mayor.py` solo hace `SELECT` sobre `partida JOIN asiento`; no hay tablas de mayor |
| **C5** | El Balance se genera de lo contabilizado | Misma fuente, y `cruzar_con_libro_mayor()` compara cuenta por cuenta |
| **C6** | Se deben diferenciar cuentas de naturaleza deudora y acreedora | Naturaleza validada contra la clasificación, con excepción para las contrapasivas |

Los seis se pueden ver en vivo en la vista **Controles** de la aplicación.

---

## Evidencia de funcionamiento

Datos de prueba de la sección 19: comercio mayorista de mercaderías, abril de
2026. La razón social no viene precargada: en el modo individual la toma el
usuario que entra («Franco») y en el modo empresa se completa en el acceso.
El RUC, la actividad y el ejercicio quedan editables desde el formulario de la
empresa.

### 1. Plan de cuentas (sección 13)

27 cuentas. Las 21 del enunciado más el **bloque de IVA** de Paraguay
(Ley N.º 1259/97). El enunciado original tiene: Activo (Caja, Banco, Clientes,
Mercaderías, Muebles y Equipos, Depreciación Acumulada), Pasivo (Proveedores),
Patrimonio Neto (Capital), Ingresos (Ventas), Costos (Costo de Mercaderías
Vendidas) y Gastos (Administrativos, de Ventas).

El bloque de IVA agrega seis cuentas que cubren el ciclo completo del impuesto:

| Código | Cuenta | Naturaleza | Rol |
|--------|--------|------------|-----|
| 1.1.05 | IVA Crédito Fiscal | Deudora | IVA pagado en las compras |
| 1.1.06 | IVA a Recuperar | Deudora | saldo a favor cuando el crédito supera al débito |
| 2.1.02 | IVA Débito Fiscal | Acreedora | IVA cobrado en las ventas |
| 2.1.03 | IVA por Pagar | Acreedora | saldo a favor del fisco al liquidar |
| 4.1.01 | Ventas Gravadas | Acreedora | ventas alcanzadas por el impuesto |
| 4.1.02 | Ventas Exentas | Acreedora | ventas no alcanzadas (Art. 15 y sgtes.) |

`4.1 Ventas` queda como cuenta **agrupadora** de las dos últimas: así se ve el
total vendido sin perder el detalle de cuánto fue gravado y cuánto exento.

### 2. Libro Diario (sección 14) — las 15 operaciones

| N. | Fecha | Concepto | Debe | Haber |
|----|-------|----------|------|-------|
| 1 | 2026-04-01 | Aporte inicial de los socios en cuenta bancaria | G. 50.000.000,00 | G. 50.000.000,00 |
| 2 | 2026-04-02 | Aporte de fondos para la caja chica | G. 12.000.000,00 | G. 12.000.000,00 |
| 3 | 2026-04-03 | Compra de mercaderías pagada al contado con efectivo | G. 20.000.000,00 | G. 20.000.000,00 |
| 4 | 2026-04-05 | Compra de mercaderías a crédito | G. 15.000.000,00 | G. 15.000.000,00 |
| 5 | 2026-04-06 | Venta de mercaderías pagada al contado con efectivo | G. 12.000.000,00 | G. 12.000.000,00 |
| 6 | 2026-04-07 | Venta de mercaderías a crédito | G. 18.000.000,00 | G. 18.000.000,00 |
| 7 | 2026-04-08 | Cobro a clientes | G. 9.000.000,00 | G. 9.000.000,00 |
| 8 | 2026-04-10 | Pago a proveedores | G. 7.000.000,00 | G. 7.000.000,00 |
| 9 | 2026-04-11 | Pago de gastos administrativos del período | G. 1.500.000,00 | G. 1.500.000,00 |
| 10 | 2026-04-12 | Pago de gastos de ventas del período | G. 900.000,00 | G. 900.000,00 |
| 11 | 2026-04-14 | Compra de muebles y equipos a crédito | G. 5.000.000,00 | G. 5.000.000,00 |
| 12 | 2026-04-15 | Depreciación de muebles y equipos del período | G. 250.000,00 | G. 250.000,00 |
| 13 | 2026-04-18 | Venta de mercaderías con depósito directo en el banco | G. 15.000.000,00 | G. 15.000.000,00 |
| 14 | 2026-04-20 | Venta de mercaderías a crédito | G. 10.000.000,00 | G. 10.000.000,00 |
| 15 | 2026-04-25 | Costo de mercaderías vendidas del período | G. 14.000.000,00 | G. 14.000.000,00 |
| | | **TOTALES** | **G. 189.650.000,00** | **G. 189.650.000,00** |

El control 1 se verifica sobre el total general:
**G. 189.650.000,00 = G. 189.650.000,00**.

### 3. Libro Mayor (sección 15) — cuenta 1.1.02 Banco

El Libro Mayor no se carga: se calcula a partir del Diario. Banco aparece en
cinco asientos y su saldo se acumula línea por línea.

| Fecha | N. | Concepto | Debe | Haber | Saldo |
|-------|---|----------|------|-------|-------|
| 2026-04-01 | 1 | Aporte inicial de los socios en cuenta bancaria | G. 50.000.000,00 | G. 0,00 | G. 50.000.000,00 |
| 2026-04-02 | 2 | Aporte de fondos para la caja chica | G. 0,00 | G. 12.000.000,00 | G. 38.000.000,00 |
| 2026-04-08 | 7 | Cobro a clientes | G. 9.000.000,00 | G. 0,00 | G. 47.000.000,00 |
| 2026-04-10 | 8 | Pago a proveedores | G. 7.000.000,00 | G. 0,00 | G. 54.000.000,00 |
| 2026-04-18 | 13 | Venta de mercaderías con depósito directo en el banco | G. 15.000.000,00 | G. 0,00 | G. 69.000.000,00 |
| | | **Totales** | **G. 81.000.000,00** | **G. 12.000.000,00** | **G. 69.000.000,00** |

### 4. Balance de comprobación (sección 16)

| Código | Cuenta | Débitos | Créditos | Saldo Deudor | Saldo Acreedor |
|--------|--------|---------|----------|--------------|----------------|
| 1.1.01 | Caja | G. 24.000.000,00 | G. 22.400.000,00 | G. 1.600.000,00 | G. 0,00 |
| 1.1.02 | Banco | G. 81.000.000,00 | G. 12.000.000,00 | G. 69.000.000,00 | G. 0,00 |
| 1.1.03 | Clientes | G. 28.000.000,00 | G. 9.000.000,00 | G. 19.000.000,00 | G. 0,00 |
| 1.1.04 | Mercaderías | G. 35.000.000,00 | G. 14.000.000,00 | G. 21.000.000,00 | G. 0,00 |
| 1.2.01 | Muebles y Equipos | G. 5.000.000,00 | G. 0,00 | G. 5.000.000,00 | G. 0,00 |
| 1.2.02 | Depreciación Acumulada | G. 0,00 | G. 250.000,00 | G. 0,00 | G. 250.000,00 |
| 2.1.01 | Proveedores | G. 0,00 | G. 27.000.000,00 | G. 0,00 | G. 27.000.000,00 |
| 3.1 | Capital | G. 0,00 | G. 50.000.000,00 | G. 0,00 | G. 50.000.000,00 |
| 4.1 | Ventas | G. 0,00 | G. 55.000.000,00 | G. 0,00 | G. 55.000.000,00 |
| 5.1 | Costo de Mercaderías Vendidas | G. 14.000.000,00 | G. 0,00 | G. 14.000.000,00 | G. 0,00 |
| 6.1 | Gastos Administrativos | G. 1.750.000,00 | G. 0,00 | G. 1.750.000,00 | G. 0,00 |
| 6.2 | Gastos de Ventas | G. 900.000,00 | G. 0,00 | G. 900.000,00 | G. 0,00 |
| | **TOTALES** | **G. 189.650.000,00** | **G. 189.650.000,00** | **G. 132.250.000,00** | **G. 132.250.000,00** |

Las dos columnas de saldos dan el mismo total (**G. 132.250.000,00**), que es
la prueba de que el Balance cuadra.

### 5. Subtotales por clasificación (control 6)

| Clasificación | Naturaleza | Débitos | Créditos | Saldo |
|---------------|------------|---------|----------|-------|
| Activo | Deudora | 173.000.000,00 | 57.650.000,00 | 115.350.000,00 |
| Pasivo | Acreedora | 0,00 | 27.000.000,00 | 27.000.000,00 |
| Patrimonio Neto | Acreedora | 0,00 | 50.000.000,00 | 50.000.000,00 |
| Ingresos | Acreedora | 0,00 | 55.000.000,00 | 55.000.000,00 |
| Costos | Deudora | 14.000.000,00 | 0,00 | 14.000.000,00 |
| Gastos | Deudora | 2.650.000,00 | 0,00 | 2.650.000,00 |

Nótese que el Activo acumula en una sola línea el saldo deudor de las cuentas
de activo y el saldo acreedor de la Depreciación Acumulada (57.650.000 de
créditos en la clasificación Activo son la contrapasiva).

Como comprobación extra, aunque el balance de comprobación no la exige:
el resultado del período es 55.000.000 − 14.000.000 − 2.650.000 =
**G. 38.350.000,00**, y

```
Activo 115.350.000 = Pasivo 27.000.000 + Capital 50.000.000 + Resultado 38.350.000
```

### 6. Trazabilidad (controles 4 y 5)

| Reporte | De dónde lee | Carga manual |
|---------|--------------|--------------|
| Libro Diario | tablas `asiento`, `partida`, `cuenta` | Sí (es la fuente) |
| Libro Mayor | `partida JOIN asiento` con `estado = 'CONFIRMADO'` | Imposible |
| Balance de comprobación | `partida JOIN asiento` con `estado = 'CONFIRMADO'` | Imposible |

`cruzar_con_libro_mayor()` compara los saldos del Balance con los del Libro
Mayor cuenta por cuenta y devuelve `coincide: True` con la lista de
diferencias vacía. Esa es la demostración de los controles 4 y 5.

### 7. IVA de Paraguay (Ley N.º 1259/97)

El módulo `erp/iva.py` resuelve las tres operaciones con IVA. Todas aceptan la
**base imponible** o el **importe total** (con el impuesto incluido), que es lo
que trae una factura.

**Venta de G. 1.000.000 al 10 %**, cobrada en efectivo:

| Cuenta | Debe | Haber |
|--------|------|-------|
| 1.1.01 Caja | G. 1.100.000,00 | |
| 4.1.01 Ventas Gravadas | | G. 1.000.000,00 |
| 2.1.02 IVA Débito Fiscal | | G. 100.000,00 |

**Compra de G. 500.000 al 10 %**, pagada en efectivo:

| Cuenta | Debe | Haber |
|--------|------|-------|
| 1.1.04 Mercaderías | G. 500.000,00 | |
| 1.1.05 IVA Crédito Fiscal | G. 50.000,00 | |
| 1.1.01 Caja | | G. 550.000,00 |

**Liquidación del período** (Formulario 120 de la SET): se cruza el Débito
Fiscal contra el Crédito Fiscal. Si el débito supera al crédito queda un saldo
a pagar en `2.1.03 IVA por Pagar`; si el crédito supera al débito, queda
`1.1.06 IVA a Recuperar`.

Con los dos asientos anteriores: débito G. 100.000 − crédito G. 50.000 =
**G. 50.000,00 a pagar**.

El impuesto se guarda siempre en **centavos enteros** y el redondeo es
comercial (medio centavo sube), que es el criterio de la declaración jurada.
Una operación **exenta** (alícuota 0) no genera línea de IVA y va a la cuenta
`4.1.02 Ventas Exentas`.

---

## La API

La interfaz no tiene lógica contable propia: toda la contabilidad vive en los
módulos de `erp/`. La API es JSON y traduce los errores del dominio a
códigos HTTP con sentido.

| Método | Ruta | Qué hace |
|--------|------|----------|
| GET / PUT | `/api/empresa` | Datos de la empresa |
| GET | `/api/resumen` | Indicadores del panel |
| GET / POST | `/api/cuentas` | Listar / crear cuentas |
| GET / PUT / DELETE | `/api/cuentas/{código}` | Consultar / editar / eliminar |
| POST | `/api/cuentas/{código}/estado` | Activar o desactivar |
| GET | `/api/cuentas/{código}/mayor` | Libro Mayor de una cuenta |
| GET / POST | `/api/asientos` | Listar / crear asientos |
| GET / PUT / DELETE | `/api/asientos/{id}` | Consultar / editar / borrar |
| POST | `/api/asientos/{id}/confirmar` | Contabilizar un borrador |
| POST | `/api/asientos/{id}/anular` | Anular con motivo |
| GET | `/api/libro-mayor` | Libro Mayor completo |
| GET | `/api/balance` | Balance de comprobación |
| GET | `/api/balance/cruce` | Cruce Balance ↔ Libro Mayor |
| GET | `/api/controles` | Estado de los 6 controles |
| GET | `/api/iva/alicuotas` | Alícuotas vigentes (10 %, 5 %, exenta) |
| POST | `/api/iva/calcular` | Calcular / discriminar el IVA de un importe |
| POST | `/api/iva/asiento` | Generar y contabilizar el asiento de venta o compra |
| GET | `/api/iva/resumen` | Débito y Crédito Fiscal del período |
| POST | `/api/iva/liquidar` | Contabilizar la liquidación del período |
| GET | `/api/asistente/configuracion` | Estado de los proveedores de la IA |
| POST | `/api/asistente` | Consultar a Pira Pires (acepta `archivos` en base64) |
| POST | `/api/importar/cuentas` | Validar (vista previa) o importar un plan de cuentas (`csv` o `xlsx` en base64) |
| POST | `/api/importar/asientos` | Validar (vista previa) o importar asientos (`csv` o `xlsx` en base64) |
| GET | `/api/exportar/cuentas?formato=csv\|xlsx` | Descargar el plan de cuentas |
| GET | `/api/exportar/asientos?formato=csv\|xlsx` | Descargar los asientos confirmados (acepta `desde` y `hasta`) |
| GET | `/api/plantillas/{cuentas\|asientos}.xlsx` | Plantillas del importador en Excel |
| POST | `/api/excel/desde-csv` | Convertir a Excel un CSV (lo usa la tarjeta de asientos del chat) |
| POST | `/api/datos-demo` | Cargar las 15 operaciones |
| POST | `/api/reiniciar` | Vaciar la base |

Códigos de error: **422** regla de negocio infringida (el cuerpo trae
`regla: "C1".."C6"` y el detalle), **404** no existe, **409** operación no
permitida sobre ese estado, **400** otro error del dominio, **500** error
interno.

Ejemplo de un error del control 1:

```console
$ curl -s -X POST localhost:8000/api/asientos -H 'Content-Type: application/json' \
    -d '{"fecha":"2026-05-01","concepto":"Descuadre","confirmar":true,
         "partidas":[{"cuenta":"1.1.01","debe":5000,"haber":0},
                     {"cuenta":"3.1","debe":0,"haber":4000}]}'
```
```json
{
  "regla": "C1",
  "titulo_regla": "Control 1 - El asiento debe cuadrar (Debe = Haber)",
  "error": "El asiento N.º 16 se guardó como BORRADOR y no pudo contabilizarse: ...",
  "detalles": ["El asiento no cuadra: total Debe G. 50,00 vs total Haber G. 40,00. ..."]
}
```

---

## Notas

- El servidor escucha solo en `127.0.0.1`: es una herramienta de estudio,
  no un servicio expuesto a la red.
- La base de datos es un archivo SQLite en `datos/erp.db`. Se puede borrar
  y empezar de cero, o usar `--datos otra.db` para trabajar en paralelo.
- Los estados contables de la sección 17 (Estado de Situación Patrimonial y
  Estado de Resultados) quedan **fuera del alcance**: el enunciado pide las
  etapas 1 a 3.
- El módulo de **IVA** y el **asistente Pira Pires** son extensiones sobre el
  enunciado: agregan el impuesto al valor agregado de Paraguay y una ayuda
  contable con IA, sin tocar los controles 1 a 6.
- Las claves de los proveedores de IA **no van al repositorio**: se leen de
  `claves.env` (ignorado por git) o de variables de entorno. El sistema
  funciona igual sin ellas; solo se desactiva el asistente.
