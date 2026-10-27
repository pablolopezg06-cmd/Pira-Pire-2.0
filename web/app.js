/* =============================================================================
 * Mini ERP Contable — lógica de la interfaz
 * =============================================================================
 * Se comunica con el servidor Python mediante la API JSON de /api.
 * No usa ningún framework: solo HTML, Tailwind CSS v4, `ui.js` y este archivo.
 *
 * Convenciones:
 *   · Los importes viajan como enteros en CENTAVOS (ver erp/dinero.py).
 *   · `estado` es el objeto de estado global de la aplicación.
 *   · Los errores del servidor se muestran con el código del control
 *     infringido (C1..C6) para conectar el error con la sección 18.
 *   · Toda la appearance viene de los componentes de ui.js, que replican la
 *     API de TailGrids (variant, appearance, size, iconOnly, pending...).
 * ========================================================================== */

// ============================================================================
// Utilidades generales
// ============================================================================

const $ = (sel, raiz = document) => raiz.querySelector(sel);
const $$ = (sel, raiz = document) => Array.from(raiz.querySelectorAll(sel));

/** Atajo al escapador de ui.js: evita inyecciones y rompe la vista. */
const esc = UI.escapar;

/** Formatea centavos como texto contable: 150000050 -> "G. 1.500.000,50". */
function fmt(centavos, conSimbolo = true) {
  const valor = (Number(centavos) || 0) / 100;
  const texto = new Intl.NumberFormat('es-PY', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(valor);
  return conSimbolo && estado.moneda ? `${estado.moneda} ${texto}` : texto;
}

/** Formatea una fecha ISO (AAAA-MM-DD) como DD/MM/AAAA. */
function fmtFecha(iso) {
  if (!iso) return '';
  const [a, m, d] = String(iso).split('-');
  return d ? `${d}/${m}/${a}` : iso;
}

/**
 * Interpreta lo que el usuario escribe en un campo de importe.
 * Acepta "1.500.000,50", "1,500,000.50", "1500000", "G. 1.500.000", etc.
 * Devuelve un número en la unidad monetaria (no en centavos).
 */
function leerMonto(valor) {
  let texto = String(valor ?? '').trim().replace(/\s/g, '');
  if (!texto) return 0;

  // 1) Se quitan los símbolos de moneda y cualquier letra: "G. 1.500.000" -> "1.500.000"
  texto = texto.replace(/[A-Za-z$€₲]/g, '');
  if (!texto) return 0;

  // 2) Se decide qué separador es decimal según cuál aparece más a la derecha.
  const ultimoPunto = texto.lastIndexOf('.');
  const ultimaComa = texto.lastIndexOf(',');
  if (ultimoPunto >= 0 && ultimaComa >= 0) {
    texto = ultimoPunto > ultimaComa
      ? texto.replace(/,/g, '')                       // coma = miles
      : texto.replace(/\./g, '').replace(',', '.');   // punto = miles
  } else if (ultimoPunto >= 0 || ultimaComa >= 0) {
    // Un único separador es ambiguo: puede ser decimal ("10.5", "0,50") o de
    // miles ("500.000", "1.500"). Se resuelve con la convención de Paraguay y
    // Argentina: si el separador está seguido de **exactamente tres dígitos**
    // es de miles; en cualquier otro caso es decimal. Es lo que se ve en los
    // importes del propio ERP ("G. 1.500.000,00").
    const separador = ultimoPunto >= 0 ? '.' : ',';
    const posicion = separador === '.' ? ultimoPunto : ultimaComa;
    const digitosDetras = texto.length - posicion - 1;
    const esMiles = digitosDetras === 3;
    texto = esMiles
      ? texto.replace(/[.,]/g, '')
      : texto.replace(separador, '.');
  }

  texto = texto.replace(/[^0-9.\-]/g, '');
  const numero = Number.parseFloat(texto);
  return Number.isFinite(numero) ? numero : 0;
}

/** Formatea un importe en el campo para que se lea mientras se tipea. */
function escribirMonto(campo, centavos) {
  campo.value = (Number(centavos) || 0) === 0
    ? ''
    : (Number(centavos) / 100).toLocaleString('es-PY', { maximumFractionDigits: 2 });
}

/** Retrasa la ejecución de una función (para los campos de búsqueda). */
function debounce(funcion, espera) {
  let temporizador;
  return (...argumentos) => {
    clearTimeout(temporizador);
    temporizador = setTimeout(() => funcion(...argumentos), espera);
  };
}

// ============================================================================
// Estado global
// ============================================================================

const estado = {
  vista: 'panel',
  empresa: null,
  moneda: 'G.',
  cuentas: [],          // plan de cuentas completo
  detalle: [],          // solo cuentas de detalle activas (para los selects)
  periodo: { desde: '', hasta: '' },
  asientoEditando: null,
  lineas: [],           // líneas del asiento en edición
  cuentaEditando: null,
  conversacion: [],     // historial enviado al asistente {role, content}
  asistenteListo: false, // la configuración ya se consultó
  ivaListo: false,      // el catálogo de alícuotas ya se consultó
};

// ============================================================================
// Acceso a la API
// ============================================================================

/**
 * Llama a un endpoint de la API.
 * Si el servidor devuelve un error de validación, lanza una excepción con
 * la información del control infringido para poder mostrarla.
 */
async function api(ruta, opciones = {}) {
  const config = { headers: {}, ...opciones };
  if (config.body && typeof config.body !== 'string') {
    config.headers['Content-Type'] = 'application/json';
    config.body = JSON.stringify(config.body);
  }

  const respuesta = await fetch(ruta, config);
  const texto = await respuesta.text();
  let datos;
  try {
    datos = texto ? JSON.parse(texto) : {};
  } catch {
    throw { mensaje: `Respuesta ilegible del servidor (${respuesta.status}).` };
  }

  if (!respuesta.ok) {
    throw {
      mensaje: datos.error || `Error ${respuesta.status}`,
      regla: datos.regla || null,
      titulo: datos.titulo_regla || null,
      detalles: datos.detalles || [],
    };
  }
  return datos;
}

/**
 * Muestra un aviso con el componente Toast.
 * @param {string} mensaje
 * @param {'success'|'danger'|'warning'|'info'} variante
 * @param {string|null} titulo
 * @param {string[]} detalles
 */
function avisar(mensaje, variante = 'info', titulo = null, detalles = []) {
  return UI.toast({
    variant: variante,
    title: titulo ?? (variante === 'success' ? 'Listo' : 'Aviso'),
    description: mensaje,
    detalles,
  });
}

/** Muestra el error de la API con el código de control correspondiente. */
function avisarError(error) {
  const titulo = error.regla
    ? `Regla infringida: ${error.regla}`
    : (error.titulo || 'No se pudo completar la operación');
  UI.toast({
    variant: 'danger',
    title: titulo,
    description: error.mensaje,
    detalles: error.detalles || [],
  });
}

// ============================================================================
// Navegación entre vistas
// ============================================================================

const VISTAS = ['panel', 'cuentas', 'diario', 'mayor', 'balance', 'iva',
                'controles', 'asistente'];

/** Carga los datos de la vista indicada la primera vez que se muestra. */
function refrescarVista(vista) {
  if (vista === 'panel') cargarPanel();
  if (vista === 'cuentas') cargarCuentas();
  if (vista === 'diario') {
    // La primera vez se prepara el formulario con dos líneas vacías.
    if (!$('#lineas-asiento')) iniciarFormulario();
    cargarCuentas();
    cargarDiario();
  }
  if (vista === 'mayor') { cargarCuentas(); cargarMayor(); }
  if (vista === 'balance') cargarBalance();
  if (vista === 'iva') { cargarCuentas(); cargarIVA(); }
  if (vista === 'controles') cargarControles();
  if (vista === 'asistente') cargarAsistente();
}

/**
 * Muestra una vista y trae sus datos.
 * Los paneles y las pestañas los administra UI.conectarTabsSincronizados:
 * esta función solo carga contenido y sube la pantalla.
 */
function irA(vista, moverFoco = false) {
  if (!VISTAS.includes(vista)) vista = 'panel';
  estado.vista = vista;
  navegacion.irA(vista, moverFoco);
  window.scrollTo({ top: 0, behavior: moverFoco ? 'auto' : 'smooth' });
  // Los datos los pide `seleccionar`: así el clic sobre la pestaña y esta
  // función terminan en un único punto de carga, sin pedir todo dos veces.
}

/** Objeto de navegación, lo crea iniciar(). */
let navegacion = { irA: () => {} };

// ============================================================================
// Arranque
// ============================================================================

/**
 * ¿La interfaz ya conectó sus manejadores?
 *
 * `iniciar()` se vuelve a llamar cada vez que el usuario entra (salir y
 * volver a entrar). Los manejadores, en cambio, se conectan **una sola vez**:
 * si el `pointerup` de la burbuja se atendiera dos veces, un solo toque abriría
 * y cerraría el chat de un tirón y la burbuja parecería estar muerta.
 */
let appConectada = false;
/** ¿Ya se pueden pedir datos a la API al cambiar de vista? */
let datosListos = false;

async function iniciar() {
  if (!appConectada) {
    appConectada = true;
    conectarApp();
  } else {
    // Al volver a entrar, la burbuja de Pira Pires reaparece: si en la sesión
    // anterior el usuario la había quitado, no se lo dejamos en el molde.
    piraEliminada = false;
    $('#pira-envoltorio')?.classList.remove('oculto');
    $('#pira-burbuja')?.classList.remove('oculto');
  }

  await cargarEmpresa();
  await cargarCuentas();
  // La burbuja de Pira Pires vive fuera de las pestañas: se conecta una sola
  // vez acá, así está disponible desde cualquier vista.
  await cargarPiraFlotante();
  datosListos = true;
  irA('panel');
}

/**
 * Conecta los menús, los botones y la burbuja. Corre una sola vez por página.
 *
 * Todo lo que se conecta con `addEventListener` va acá: si se conectara dos
 * veces (por ejemplo, al salir y volver a entrar), cada toque o clic se
 * atendería dos veces y la interfaz parecería no responder.
 */
function conectarApp() {
  // Los dos menús (lateral y móvil) controlan los mismos paneles y quedan
  // sincronizados: con las flechas se recorren dentro de cada menú.
  // Al elegir una pestaña (con el mouse o con las flechas) se cargan sus datos:
  // antes el cambio de vista solo guardaba el estado y el panel quedaba vacío.
  // `datosListos` evita que la llamada inicial de conectarTabsSincronizados
  // pida datos antes de que estén cargados la empresa y el plan de cuentas.
  navegacion = UI.conectarTabsSincronizados({
    grupos: [$$('#menu [role="tab"]'), $$('#menu-movil [role="tab"]')],
    seleccionar: (vista) => {
      estado.vista = vista;
      // En la pestaña del asistente la burbuja flotante sobra: está el mismo
      // chat a pantalla completa. Se esconde para no tapar el contenido y
      // vuelve sola al salir de esa pestaña.
      actualizarBurbujaSegunVista(vista);
      if (datosListos) refrescarVista(vista);
    },
  });

  $('#btn-refrescar').onclick = () => refrescarVista(estado.vista);
  $('#filtro-desde').onchange = leerPeriodo;
  $('#filtro-hasta').onchange = leerPeriodo;
  conectarImportadores();
}

// ============================================================================
// Tema claro / oscuro
// ============================================================================
//
// El tema se guarda como `data-tema="oscuro"` en el <html>. Las variables CSS
// del tema oscuro viven en `index.html` (bloque `[data-tema="oscuro"]`), así
// que acá solo se prende y se apaga. La elección se recuerda entre visitas y,
// la primera vez, se respeta lo que pida el sistema operativo.

/** Clave de `localStorage` donde se recuerda el tema elegido. */
const CLAVE_TEMA = 'erp-tema';

/** Lee el tema guardado, o el del sistema si nunca se eligió uno. */
function leerTemaGuardado() {
  try {
    const guardado = localStorage.getItem(CLAVE_TEMA);
    if (guardado === 'claro' || guardado === 'oscuro') return guardado;
  } catch {
    // Sin almacenamiento: se cae al del sistema.
  }
  const sistemaOscuro = window.matchMedia
    && window.matchMedia('(prefers-color-scheme: dark)').matches;
  return sistemaOscuro ? 'oscuro' : 'claro';
}

/** Aplica un tema al documento y actualiza el botón. */
function aplicarTema(tema) {
  const oscuro = tema === 'oscuro';
  document.documentElement.setAttribute('data-tema', tema);
  document.documentElement.style.colorScheme = oscuro ? 'dark' : 'light';

  const boton = $('#btn-tema');
  const icono = $('#btn-tema-icono');
  if (icono) {
    // El icono muestra a qué se va a cambiar, no el estado actual: con el
    // tema claro se ve la luna («pasá a oscuro»).
    icono.innerHTML = UI.icono(oscuro ? 'sol' : 'luna', { clase: 'size-4' });
  }
  if (boton) {
    const etiqueta = oscuro ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro';
    boton.setAttribute('aria-label', etiqueta);
    boton.setAttribute('aria-pressed', String(oscuro));
    boton.title = etiqueta;
  }
}

/** Alterna el tema y lo recuerda. */
function alternarTema() {
  const actual = document.documentElement.getAttribute('data-tema');
  const nuevo = actual === 'oscuro' ? 'claro' : 'oscuro';
  aplicarTema(nuevo);
  try {
    localStorage.setItem(CLAVE_TEMA, nuevo);
  } catch {
    // Sin almacenamiento: el tema vale solo para esta visita.
  }
}

/** Prepara el botón de tema y aplica el tema inicial. */
function iniciarTema() {
  aplicarTema(leerTemaGuardado());
  const boton = $('#btn-tema');
  if (boton) boton.onclick = alternarTema;
}

/** Lee el filtro de período de la barra superior. */
function leerPeriodo() {
  estado.periodo = {
    desde: $('#filtro-desde').value,
    hasta: $('#filtro-hasta').value,
  };
  if (estado.vista === 'mayor') cargarMayor();
  if (estado.vista === 'balance') cargarBalance();
  if (estado.vista === 'diario') cargarDiario();
}

/** Construye los parámetros de período para las consultas. */
function conPeriodo(extra = {}) {
  const params = new URLSearchParams({
    ...(estado.periodo.desde ? { desde: estado.periodo.desde } : {}),
    ...(estado.periodo.hasta ? { hasta: estado.periodo.hasta } : {}),
    ...extra,
  });
  const texto = params.toString();
  return texto ? `?${texto}` : '';
}

// ============================================================================
// Vista: Panel
// ============================================================================

async function cargarEmpresa() {
  try {
    estado.empresa = await api('/api/empresa');
    estado.moneda = estado.empresa.moneda || 'G.';

    const sesion = leerSesion();
    const perfil = perfilDeSesion();
    // En modo empresa manda la razón social; en modo individual se mantiene el
    // nombre del usuario, como en la prueba de un solo usuario.
    const nombreMostrado = perfil
      ? (estado.empresa.nombre || perfil.razonSocial || 'Mi empresa')
      : (sesion?.usuario || estado.empresa.nombre || 'Mi empresa');
    $('#empresa-nombre').textContent = nombreMostrado;
    $('#empresa-detalle').textContent = [
      estado.empresa.cuit && `RUC ${estado.empresa.cuit}`,
      perfil?.tipoNombre,
      estado.empresa.actividad,
      estado.empresa.ejercicio_inicio &&
        `Ejercicio ${fmtFecha(estado.empresa.ejercicio_inicio)} – ${fmtFecha(estado.empresa.ejercicio_fin)}`,
    ].filter(Boolean).join(' · ');

    $('#filtro-desde').value = estado.empresa.ejercicio_inicio || '';
    $('#filtro-hasta').value = estado.empresa.ejercicio_fin || '';
    estado.periodo = { desde: $('#filtro-desde').value, hasta: $('#filtro-hasta').value };

    pintarFormularioEmpresa();
    pintarPerfilEmpresa();
  } catch (error) {
    avisarError(error);
  }
}

function pintarFormularioEmpresa() {
  // En modo empresa manda la razón social que se cargó en el acceso y se
  // puede editar desde acá. En modo individual se mantiene el nombre del
  // usuario que entró, bloqueado, como en la prueba de un solo usuario.
  const perfil = perfilDeSesion();
  const usuario = perfil ? '' : nombreDeSesion();
  const campos = [
    ['nombre', 'Nombre o razón social', 'text'],
    ['cuit', 'RUC', 'text'],
    ['actividad', 'Actividad principal', 'text'],
    ['ejercicio_inicio', 'Inicio del ejercicio', 'date'],
    ['ejercicio_fin', 'Fin del ejercicio', 'date'],
    ['moneda', 'Símbolo de moneda', 'text'],
  ];
  $('#form-empresa').innerHTML = campos.map(([clave, etiqueta, tipo]) => {
    const valor = clave === 'nombre' && usuario
      ? usuario
      : (estado.empresa?.[clave] ?? '');
    return UI.campoHTML({
      id: `emp-${clave}`,
      label: etiqueta,
      control: UI.inputHTML({
        id: `emp-${clave}`, name: clave, type: tipo, value: valor,
        disabled: Boolean(clave === 'nombre' && usuario),
      }),
    });
  }).join('')
    + UI.botonHTML({
      label: 'Guardar datos de la empresa',
      attributes: 'type="submit"',
      className: 'w-full',
    });

  $('#form-empresa').onsubmit = async (evento) => {
    evento.preventDefault();
    // Estado pending del componente Button: se atenúa, marca aria-busy y
    // conserva el foco en lugar de desaparecer del recorrido del teclado.
    const boton = evento.target.querySelector('button[type="submit"]');
    boton.classList.add('btn-pending');
    boton.setAttribute('aria-busy', 'true');
    try {
      const datos = Object.fromEntries(new FormData(evento.target).entries());
      if (usuario) datos.nombre = usuario;
      estado.empresa = await api('/api/empresa', { method: 'PUT', body: datos });
      estado.moneda = estado.empresa.moneda || 'G.';
      // La cuenta de acceso también se actualiza: el próximo ingreso vuelve a
      // dejar la empresa tal como quedó guardada acá.
      if (perfil) actualizarCuentaEmpresa(datos);
      avisar('Datos de la empresa actualizados.', 'success');
      await cargarPanel();
      pintarPerfilEmpresa();
      $('#empresa-nombre').textContent = usuario || estado.empresa.nombre;
      $('#empresa-detalle').textContent = [
        estado.empresa.cuit && `RUC ${estado.empresa.cuit}`,
        perfilDeSesion()?.tipoNombre,
        estado.empresa.actividad,
      ].filter(Boolean).join(' · ');
    } catch (error) {
      avisarError(error);
    } finally {
      boton.classList.remove('btn-pending');
      boton.removeAttribute('aria-busy');
    }
  };
}

/** Tonos de los indicadores, según el estado del control que muestran. */
const TONO_KPI = {
  primary: 'border-primary-200 bg-primary-50',
  success: 'border-success-200 bg-success-50',
  danger:  'border-danger-200 bg-danger-50',
  neutral: 'border-surface-200 bg-surface-50',
};

async function cargarPanel() {
  try {
    const [resumen, controles] = await Promise.all([
      api('/api/resumen'),
      api('/api/controles'),
    ]);

    const cuadre = resumen.cuadre_libro_diario;
    const totales = resumen.totales_balance;

    const indicadores = [
      {
        titulo: 'Asientos contabilizados',
        valor: cuadre.asientos_confirmados,
        detalle: `${cuadre.asientos_borrador} borrador(es) · ${cuadre.asientos_anulados} anulado(s)`,
        tono: cuadre.asientos_confirmados > 0 ? 'primary' : 'neutral',
      },
      {
        titulo: 'Total de débitos',
        valor: fmt(cuadre.total_debe),
        detalle: cuadre.asientos_confirmados > 0
          ? 'Suma de todos los asientos confirmados'
          : 'Todavía no hay asientos confirmados',
        tono: cuadre.total_debe > 0 ? 'primary' : 'neutral',
      },
      {
        titulo: 'Total de créditos',
        valor: fmt(cuadre.total_haber),
        detalle: cuadre.cuadra
          ? 'Control 1 · idéntico a los débitos'
          : 'Control 1 · NO coincide con los débitos',
        tono: cuadre.cuadra ? 'success' : 'danger',
      },
      {
        titulo: 'Balance',
        valor: resumen.balance_cuadrado ? 'Cuadra' : 'No cuadra',
        detalle: `${resumen.cuentas_detalle} cuentas de detalle · ` +
          `saldos ${fmt(totales.saldo_deudor, false)} = ${fmt(totales.saldo_acreedor, false)}`,
        tono: resumen.balance_cuadrado ? 'success' : 'danger',
      },
    ];

    $('#panel-indicadores').innerHTML = indicadores.map((i) => `
      <div class="kpi ${TONO_KPI[i.tono]}">
        <p class="text-xs font-medium text-surface-500">${esc(i.titulo)}</p>
        <p class="mt-1 text-2xl font-semibold tracking-tight text-surface-900 tabular-nums">${esc(i.valor)}</p>
        <p class="mt-1 text-xs text-surface-500">${esc(i.detalle)}</p>
      </div>`).join('');

    $('#panel-controles').innerHTML = controles.controles.map((c) => `
      <div class="flex items-start gap-3 border-b border-surface-100 py-2.5 last:border-0">
        ${UI.badge(c.codigo, {
          variant: c.cumple ? 'success' : 'danger',
          iconName: c.cumple ? 'cheque' : 'cruz',
        })}
        <div class="min-w-0">
          <p class="text-sm font-medium text-surface-800">${esc(c.titulo)}</p>
          <p class="text-xs text-surface-500">${esc(c.detalle)}</p>
        </div>
      </div>`).join('');
  } catch (error) {
    avisarError(error);
  }
}

// ============================================================================
// Importación de datos propios desde CSV
// ============================================================================

/** El servidor acepta cuerpos de hasta 2 MB; dejamos margen para el JSON. */
const LIMITE_CSV_BYTES = 1.5 * 1024 * 1024;

/** Tabla de errores de importación, con los valores siempre escapados. */
function tablaErroresImportacion(errores) {
  const lista = Array.isArray(errores) ? errores : [];
  if (!lista.length) return '';
  const limite = 40;
  const filas = lista.slice(0, limite).map((error) => `
    <tr>
      <td class="whitespace-nowrap pr-3 align-top font-mono text-xs text-surface-500">
        ${error.fila == null ? '—' : esc(error.fila)}
      </td>
      <td class="align-top">${esc(error.mensaje || 'Error desconocido')}</td>
    </tr>`).join('');

  return `
    <div class="max-h-64 overflow-auto rounded-field border border-danger-200 bg-danger-50/40">
      <table class="table text-xs">
        <thead>
          <tr><th class="w-14">Fila</th><th>Problema</th></tr>
        </thead>
        <tbody>${filas}</tbody>
      </table>
    </div>
    ${lista.length > limite
      ? `<p class="text-xs text-surface-500">…y ${lista.length - limite} error(es) más.</p>`
      : ''}`;
}

function contenidoErroresImportacion(previa) {
  return `
    <div class="space-y-3">
      <p><strong>No se importó nada.</strong> Corregí el archivo y volvé a intentarlo.</p>
      ${tablaErroresImportacion(previa.errores)}
    </div>`;
}

function tablaMuestraCuentas(muestra) {
  const filas = (muestra || []).map((cuenta) => `
    <tr>
      <td class="whitespace-nowrap font-mono text-xs">${esc(cuenta.codigo)}</td>
      <td>${esc(cuenta.nombre)}</td>
      <td class="whitespace-nowrap">${esc(CLASIFICACION[cuenta.clasificacion] || cuenta.clasificacion)}</td>
      <td class="whitespace-nowrap">${esc(cuenta.naturaleza || '—')}</td>
      <td class="whitespace-nowrap">${cuenta.recibe_movimiento ? 'Detalle' : 'Agrupadora'}</td>
      <td class="whitespace-nowrap">${esc(cuenta.estado)}</td>
    </tr>`).join('');

  return `
    <div class="max-h-52 overflow-auto rounded-field border border-surface-200">
      <table class="table text-xs">
        <thead>
          <tr>
            <th>Código</th><th>Nombre</th><th>Clasificación</th>
            <th>Nat.</th><th>Tipo</th><th>Estado</th>
          </tr>
        </thead>
        <tbody>${filas}</tbody>
      </table>
    </div>`;
}

function contenidoPrevioCuentas(previa) {
  return `
    <div class="space-y-3">
      <p>
        Se van a crear <strong>${esc(previa.a_crear)}</strong> cuenta(s) nueva(s).
        ${previa.omitidas
          ? `Se omitirán <strong>${esc(previa.omitidas)}</strong> que ya existen.`
          : ''}
      </p>
      ${tablaMuestraCuentas(previa.muestra)}
      <p class="text-xs text-surface-500">
        Todo se guarda en una sola operación: si algo falla, no se crea ninguna cuenta.
      </p>
    </div>`;
}

function tablaMuestraAsientos(muestra) {
  const filas = (muestra || []).map((asiento) => {
    const lineas = (asiento.partidas || []).map((p) => `
      <tr>
        <td class="whitespace-nowrap pl-6 font-mono text-xs">${esc(p.cuenta)}</td>
        <td>${esc(p.nombre || '¿cuenta inexistente?')}${p.detalle
          ? `<span class="text-surface-500"> · ${esc(p.detalle)}</span>` : ''}</td>
        <td class="whitespace-nowrap text-right tabular-nums">${p.debe ? esc(fmt(p.debe, false)) : ''}</td>
        <td class="whitespace-nowrap text-right tabular-nums">${p.haber ? esc(fmt(p.haber, false)) : ''}</td>
      </tr>`).join('');
    return `
      <tr class="bg-surface-100/60 font-semibold">
        <td class="whitespace-nowrap font-mono text-xs">${esc(asiento.asiento)}</td>
        <td>${esc(fmtFecha(asiento.fecha))} · ${esc(asiento.concepto)}</td>
        <td class="whitespace-nowrap text-right tabular-nums">${esc(fmt(asiento.debe, false))}</td>
        <td class="whitespace-nowrap text-right tabular-nums">${esc(fmt(asiento.haber, false))}</td>
      </tr>${lineas}`;
  }).join('');

  return `
    <div class="max-h-72 overflow-auto rounded-field border border-surface-200">
      <table class="table text-xs">
        <thead>
          <tr>
            <th>N.º</th><th>Fecha · concepto / cuenta</th>
            <th class="text-right">Debe</th><th class="text-right">Haber</th>
          </tr>
        </thead>
        <tbody>${filas}</tbody>
      </table>
    </div>`;
}

function contenidoPrevioAsientos(previa) {
  return `
    <div class="space-y-3">
      <p>
        Se van a importar <strong>${esc(previa.asientos)}</strong> asiento(s)
        (<strong>${esc(previa.lineas)}</strong> líneas) como <strong>CONFIRMADOS</strong>.
      </p>
      <p class="text-xs text-surface-500">
        Total Debe: <strong>${esc(fmt(previa.total_debe))}</strong> ·
        Total Haber: <strong>${esc(fmt(previa.total_haber))}</strong>
      </p>
      ${tablaMuestraAsientos(previa.muestra)}
      <p class="text-xs text-surface-500">
        Todo se guarda en una sola operación: si algo falla, no se importa ningún asiento.
      </p>
    </div>`;
}

/** ¿Es una planilla de Excel moderna (.xlsx)? */
function esExcel(archivo) {
  return /\.xlsx$/i.test(archivo?.name || '')
    || archivo?.type === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
}

/**
 * Lee el archivo a importar y valida el tamaño antes de enviarlo. Devuelve
 * el cuerpo para la API: `{ csv }` con el texto, o `{ xlsx }` con el Excel
 * en base64 (el servidor lo convierte a CSV y lo valida igual).
 */
async function leerArchivoCSV(archivo) {
  if (!archivo) return null;
  if (/\.xls$/i.test(archivo.name || '')) {
    avisar('El formato .xls (Excel 97-2003) no se puede leer. Guardalo como .xlsx o CSV.',
      'danger', 'Formato no soportado');
    return null;
  }
  if (archivo.size > LIMITE_CSV_BYTES) {
    avisar(
      'El archivo supera el máximo de 1,5 MB. Dividilo en partes más chicas.',
      'danger',
      'Archivo demasiado grande',
    );
    return null;
  }
  try {
    if (esExcel(archivo)) {
      return { xlsx: await archivoABase64(archivo) };
    }
    const texto = await archivo.text();
    if (!texto.trim()) {
      avisar('El archivo está vacío.', 'danger', 'CSV vacío');
      return null;
    }
    return { csv: texto };
  } catch {
    avisar('No se pudo leer el archivo.', 'danger', 'Error al leer el archivo');
    return null;
  }
}

/** Contenido de un archivo en base64 (sin el prefijo `data:`). */
function archivoABase64(archivo) {
  return new Promise((resolver, rechazar) => {
    const lector = new FileReader();
    lector.onload = () => resolver(String(lector.result).split(',')[1] || '');
    lector.onerror = () => rechazar(lector.error);
    lector.readAsDataURL(archivo);
  });
}

async function importarCuentasCSV(archivo) {
  const contenido = await leerArchivoCSV(archivo);
  if (contenido === null) return;

  let previa;
  try {
    previa = await api('/api/importar/cuentas', {
      method: 'POST',
      body: { ...contenido, confirmar: false },
    });
  } catch (error) {
    avisarError(error);
    return;
  }

  if (!previa.valido) {
    await UI.abrirDialogo({
      titulo: 'El CSV de cuentas tiene errores',
      contenido: contenidoErroresImportacion(previa),
      confirmarTexto: 'Entendido',
      cancelarTexto: null,
      variant: 'danger',
      icono: 'alerta',
    });
    return;
  }

  if (!previa.a_crear) {
    await UI.abrirDialogo({
      titulo: 'No hay cuentas nuevas',
      contenido: `<p>Las ${esc(previa.filas)} fila(s) del archivo ya existen en el plan
        de cuentas. No se modificó nada.</p>`,
      confirmarTexto: 'Entendido',
      cancelarTexto: null,
      icono: 'info',
    });
    return;
  }

  const aceptado = await UI.abrirDialogo({
    titulo: 'Importar plan de cuentas',
    contenido: contenidoPrevioCuentas(previa),
    confirmarTexto: `Importar ${previa.a_crear} cuenta(s)`,
    cancelarTexto: 'Cancelar',
    variant: 'primary',
    icono: 'archivo',
  });
  if (!aceptado) return;

  try {
    const resultado = await api('/api/importar/cuentas', {
      method: 'POST',
      body: { ...contenido, confirmar: true },
    });
    avisar(
      `Se importaron ${resultado.creadas} cuenta(s).` +
        (resultado.omitidas ? ` Se omitieron ${resultado.omitidas} que ya existían.` : ''),
      'success',
      'Plan de cuentas actualizado',
    );
    await Promise.all([cargarCuentas(), cargarPanel()]);
  } catch (error) {
    avisarError(error);
  }
}

async function importarAsientosCSV(archivo) {
  const contenido = await leerArchivoCSV(archivo);
  if (contenido === null) return;
  await importarAsientosTexto(contenido);
}

/**
 * Vista previa y confirmación de asientos. `contenido` es `{ csv }` o
 * `{ xlsx }`. La usan el importador de archivos y la tarjeta de asientos
 * propuestos por Pira Pires. Devuelve el resultado de la importación, o
 * `null` si no se guardó nada.
 */
async function importarAsientosTexto(contenido, opciones = {}) {
  let previa;
  try {
    previa = await api('/api/importar/asientos', {
      method: 'POST',
      body: { ...contenido, confirmar: false },
    });
  } catch (error) {
    avisarError(error);
    return null;
  }

  if (!previa.valido) {
    await UI.abrirDialogo({
      titulo: opciones.tituloErrores || 'El CSV de asientos tiene errores',
      contenido: contenidoErroresImportacion(previa)
        + (opciones.ayudaErrores ? `<p class="mt-3 text-xs">${opciones.ayudaErrores}</p>` : ''),
      confirmarTexto: 'Entendido',
      cancelarTexto: null,
      variant: 'danger',
      icono: 'alerta',
    });
    return null;
  }

  if (!previa.asientos) {
    await UI.abrirDialogo({
      titulo: 'No hay asientos para importar',
      contenido: '<p>El archivo no contiene asientos con líneas.</p>',
      confirmarTexto: 'Entendido',
      cancelarTexto: null,
      icono: 'info',
    });
    return null;
  }

  const aceptado = await UI.abrirDialogo({
    titulo: opciones.titulo || 'Importar asientos',
    contenido: contenidoPrevioAsientos(previa),
    confirmarTexto: `Importar ${previa.asientos} asiento(s)`,
    cancelarTexto: 'Cancelar',
    variant: 'primary',
    icono: 'archivo',
  });
  if (!aceptado) return null;

  try {
    const resultado = await api('/api/importar/asientos', {
      method: 'POST',
      body: { ...contenido, confirmar: true },
    });
    avisar(
      `Se importaron ${resultado.creados} asiento(s) confirmados.`,
      'success',
      'Libro Diario actualizado',
    );
    await Promise.all([cargarDiario(), cargarPanel()]);
    return resultado;
  } catch (error) {
    avisarError(error);
    return null;
  }
}

/** Conecta los botones de importación y los dos inputs de archivo compartidos. */
function conectarImportadores() {
  const disparadores = [
    ['#panel-importar-cuentas', '#archivo-cuentas'],
    ['#btn-importar-cuentas', '#archivo-cuentas'],
    ['#panel-importar-asientos', '#archivo-asientos'],
    ['#btn-importar-asientos', '#archivo-asientos'],
  ];

  disparadores.forEach(([botonSelector, inputSelector]) => {
    const boton = $(botonSelector);
    if (!boton) return;
    boton.addEventListener('click', () => {
      const input = $(inputSelector);
      if (!input) return;
      input.value = '';
      input.click();
    });
  });

  const archivoCuentas = $('#archivo-cuentas');
  if (archivoCuentas) {
    archivoCuentas.addEventListener('change', async (evento) => {
      const archivo = evento.target.files?.[0] || null;
      try {
        await importarCuentasCSV(archivo);
      } finally {
        evento.target.value = '';
      }
    });
  }

  const archivoAsientos = $('#archivo-asientos');
  if (archivoAsientos) {
    archivoAsientos.addEventListener('change', async (evento) => {
      const archivo = evento.target.files?.[0] || null;
      try {
        await importarAsientosCSV(archivo);
      } finally {
        evento.target.value = '';
      }
    });
  }
}

// ============================================================================
// Vista: Plan de cuentas
// ============================================================================

async function cargarCuentas() {
  try {
    const datos = await api(
      `/api/cuentas?inactivas=${$('#cuenta-inactivas')?.checked === false ? 0 : 1}` +
      `${$('#cuenta-busqueda')?.value ? `&q=${encodeURIComponent($('#cuenta-busqueda').value)}` : ''}`
    );
    estado.cuentas = datos.cuentas;
    estado.detalle = datos.cuentas.filter(
      (c) => c.recibe_movimiento && c.estado === 'ACTIVA'
    );
    pintarCuentas();
    pintarFormularioCuenta();
    actualizarSelectoresCuenta();
  } catch (error) {
    avisarError(error);
  }
}

/** Las clasificaciones se colorean con los mismos tonos que sus naturalezas. */
const CLASIFICACION = {
  1: 'ACTIVO', 2: 'PASIVO', 3: 'PATRIMONIO NETO',
  4: 'INGRESOS', 5: 'COSTOS', 6: 'GASTOS',
};

/** El color de una cuenta depende de su naturaleza (control 6). */
function badgeNaturaleza(naturaleza, texto = null) {
  return UI.badge(texto ?? (naturaleza === 'D' ? 'Deudora' : 'Acreedora'), {
    variant: naturaleza === 'D' ? 'primary' : 'secondary',
  });
}

function pintarCuentas() {
  const cuerpo = $('#cuentas-tbody');
  if (!estado.cuentas.length) {
    cuerpo.innerHTML = `
      <tr><td colspan="6" class="py-10 text-center text-surface-400">
        No hay cuentas que coincidan con el filtro.
        <p class="mt-1 text-xs">Cargá los datos de prueba desde el Panel o creá la primera cuenta.</p>
      </td></tr>`;
    return;
  }

  const varianteClasificacion = {
    1: 'primary', 2: 'secondary', 3: 'secondary',
    4: 'success', 5: 'warning', 6: 'warning',
  };

  cuerpo.innerHTML = estado.cuentas.map((c) => {
    const sangria = (c.nivel - 1) * 14;
    const agrupadora = !c.recibe_movimiento;
    return `
      <tr>
        <td class="font-mono text-xs text-surface-600">${esc(c.codigo)}</td>
        <td style="padding-left:${12 + sangria}px" class="${agrupadora ? 'font-semibold text-surface-800' : 'font-medium'}">
          ${esc(c.nombre)}
          ${agrupadora
            ? '<span class="ml-1 text-[10px] font-normal text-surface-400">(agrupadora)</span>' : ''}
        </td>
        <td>
          ${UI.badge(`${c.clasificacion} · ${esc(c.clasificacion_nombre)}`, {
            variant: varianteClasificacion[c.clasificacion] ?? 'secondary',
          })}
        </td>
        <td>${badgeNaturaleza(c.naturaleza, esc(c.naturaleza_nombre))}</td>
        <td>
          ${UI.badge(c.estado, { variant: c.estado === 'ACTIVA' ? 'success' : 'secondary' })}
        </td>
        <td>
          <div class="flex justify-end gap-1.5">
            ${UI.botonHTML({
              label: 'Editar', size: 'xs', appearance: 'outline', variant: 'secondary',
              iconName: 'lapiz', attributes: `data-editar="${esc(c.codigo)}"`,
              title: `Editar la cuenta ${esc(c.codigo)}`,
            })}
            ${agrupadora ? '' : UI.botonHTML({
              label: c.estado === 'ACTIVA' ? 'Desactivar' : 'Activar',
              size: 'xs', appearance: 'outline',
              variant: c.estado === 'ACTIVA' ? 'warning' : 'success',
              iconName: c.estado === 'ACTIVA' ? 'prohibido' : 'tilde',
              attributes: `data-estado="${esc(c.codigo)}" data-nuevo="${c.estado === 'ACTIVA' ? 'INACTIVA' : 'ACTIVA'}"`,
            })}
            ${UI.botonHTML({
              label: 'Borrar', size: 'xs', appearance: 'outline', variant: 'danger',
              iconName: 'papelera', attributes: `data-borrar="${esc(c.codigo)}"`,
              title: `Eliminar la cuenta ${esc(c.codigo)}`,
            })}
          </div>
        </td>
      </tr>`;
  }).join('');

  UI.delegar(cuerpo, '[data-editar]', (d) => editarCuenta(d.editar));
  UI.delegar(cuerpo, '[data-estado]', (d) => cambiarEstadoCuenta(d.estado, d.nuevo));
  UI.delegar(cuerpo, '[data-borrar]', (d) => borrarCuenta(d.borrar));
}

function pintarFormularioCuenta() {
  const enEdicion = estado.cuentaEditando
    ? estado.cuentas.find((c) => c.codigo === estado.cuentaEditando)
    : null;

  $('#cuenta-form-titulo').textContent = enEdicion
    ? `Editar cuenta ${enEdicion.codigo}`
    : 'Nueva cuenta';

  $('#form-cuenta').innerHTML = `
    ${UI.alertaHTML('info', 'Cómo se codifica una cuenta',
      'El código es jerárquico (<code class="codigo">1.1.01</code>) y su primer dígito debe '
      + 'coincidir con la clasificación. Una cuenta de nivel 2 o 3 necesita que exista su cuenta padre.')}

    <div class="grid grid-cols-2 gap-3">
      ${UI.campoHTML({
        id: 'cuenta-codigo',
        label: 'Código',
        control: UI.inputHTML({
          id: 'cuenta-codigo', name: 'codigo', value: enEdicion?.codigo ?? '',
          placeholder: '1.1.05', disabled: Boolean(enEdicion), className: 'font-mono',
        }),
        description: enEdicion
          ? 'El código no se modifica: es la identidad de la cuenta.' : null,
      })}
      ${UI.campoHTML({
        id: 'cuenta-clasificacion',
        label: 'Clasificación',
        control: UI.selectHTML({
          id: 'cuenta-clasificacion', name: 'clasificacion',
          value: enEdicion?.clasificacion ?? 1,
          items: Object.entries(CLASIFICACION).map(([n, t]) => ({
            value: n, label: `${n} · ${t}`,
          })),
        }),
      })}
    </div>

    ${UI.campoHTML({
      id: 'cuenta-nombre',
      label: 'Nombre de la cuenta',
      isRequired: true,
      control: UI.inputHTML({
        id: 'cuenta-nombre', name: 'nombre', value: enEdicion?.nombre ?? '',
        placeholder: 'Gastos de publicidad',
      }),
    })}

    ${UI.campoHTML({
      id: 'cuenta-naturaleza',
      label: 'Naturaleza',
      control: UI.selectHTML({
        id: 'cuenta-naturaleza', name: 'naturaleza',
        value: enEdicion?.naturaleza ?? 'D',
        items: [{ value: 'D', label: 'D · Deudora' }, { value: 'A', label: 'A · Acreedora' }],
      }),
      description: 'Se deduce de la clasificación. Se puede cambiar solo en cuentas '
        + 'contrapasivas (la Depreciación Acumulada es un activo con saldo acreedor).',
    })}

    <div class="grid grid-cols-2 gap-3">
      ${UI.campoHTML({
        id: 'cuenta-tipo',
        label: 'Tipo de cuenta',
        control: UI.selectHTML({
          id: 'cuenta-tipo', name: 'recibe_movimiento',
          value: enEdicion ? (enEdicion.recibe_movimiento ? 1 : 0) : 1,
          items: [
            { value: 1, label: 'Detalle (admite movimientos)' },
            { value: 0, label: 'Agrupadora (solo subtotales)' },
          ],
        }),
      })}
      ${UI.campoHTML({
        id: 'cuenta-estado',
        label: 'Estado',
        control: UI.selectHTML({
          id: 'cuenta-estado', name: 'estado',
          value: enEdicion?.estado ?? 'ACTIVA',
          items: ['ACTIVA', 'INACTIVA'],
        }),
      })}
    </div>

    <div class="flex gap-2 pt-1">
      ${UI.botonHTML({
        label: enEdicion ? 'Guardar cambios' : 'Crear cuenta',
        attributes: 'type="submit"',
        className: enEdicion ? '' : 'w-full',
      })}
      ${enEdicion ? UI.botonHTML({
        label: 'Cancelar', variant: 'secondary', appearance: 'outline',
        attributes: 'id="cuenta-cancelar"',
      }) : ''}
    </div>
  `;

  // Al cambiar la clasificación se propone la naturaleza esperada (control 6).
  $('#cuenta-clasificacion').onchange = (evento) => {
    const esperadas = { 1: 'D', 2: 'A', 3: 'A', 4: 'A', 5: 'D', 6: 'D' };
    $('#cuenta-naturaleza').value = esperadas[evento.target.value];
  };

  const cancelar = $('#cuenta-cancelar');
  if (cancelar) cancelar.onclick = () => {
    estado.cuentaEditando = null;
    pintarFormularioCuenta();
  };

  $('#form-cuenta').onsubmit = guardarCuenta;
}

async function guardarCuenta(evento) {
  evento.preventDefault();
  const formulario = Object.fromEntries(new FormData(evento.target).entries());
  const cuerpo = {
    nombre: formulario.nombre,
    clasificacion: Number(formulario.clasificacion),
    recibe_movimiento: formulario.recibe_movimiento === '1',
    naturaleza: formulario.naturaleza,
    estado: formulario.estado,
  };

  try {
    if (estado.cuentaEditando) {
      await api(`/api/cuentas/${encodeURIComponent(estado.cuentaEditando)}`,
        { method: 'PUT', body: cuerpo });
      avisar(`Cuenta ${estado.cuentaEditando} actualizada.`, 'success');
      estado.cuentaEditando = null;
    } else {
      const respuesta = await api('/api/cuentas', {
        method: 'POST', body: { codigo: formulario.codigo, ...cuerpo },
      });
      avisar(`Cuenta ${respuesta.cuenta.codigo} ${respuesta.cuenta.nombre} creada.`, 'success');
    }
    await cargarCuentas();
  } catch (error) {
    avisarError(error);
  }
}

function editarCuenta(codigo) {
  estado.cuentaEditando = codigo;
  pintarFormularioCuenta();
  const nombre = $('#cuenta-nombre');
  nombre.focus();
  nombre.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function cambiarEstadoCuenta(codigo, nuevo) {
  try {
    await api(`/api/cuentas/${encodeURIComponent(codigo)}/estado`, {
      method: 'POST', body: { estado: nuevo },
    });
    avisar(`Cuenta ${codigo} ahora está ${nuevo}.`, 'success');
    await cargarCuentas();
  } catch (error) {
    avisarError(error);
  }
}

async function borrarCuenta(codigo) {
  const aceptado = await UI.confirmar({
    titulo: `Eliminar la cuenta ${codigo}?`,
    descripcion: 'Solo se puede borrar una cuenta que no tenga movimientos '
      + 'asociados ni cuentas hijas dependientes.',
    confirmarTexto: 'Eliminar',
    icono: 'papelera',
  });
  if (!aceptado) return;

  try {
    await api(`/api/cuentas/${encodeURIComponent(codigo)}`, { method: 'DELETE' });
    avisar(`Cuenta ${codigo} eliminada.`, 'success');
    estado.cuentaEditando = null;
    await cargarCuentas();
  } catch (error) {
    avisarError(error);
  }
}

// ============================================================================
// Vista: Libro Diario
// ============================================================================

function opcionesCuentas(seleccionado = '') {
  if (!estado.detalle.length) {
    return '<option value="">No hay cuentas de detalle activas</option>';
  }
  return estado.detalle.map((c) => `
    <option value="${esc(c.codigo)}" ${c.codigo === seleccionado ? 'selected' : ''}>
      ${esc(c.codigo)} · ${esc(c.nombre)} (${c.naturaleza === 'D' ? 'D' : 'A'})
    </option>`).join('');
}

async function cargarDiario() {
  try {
    const datos = await api(`/api/asientos${conPeriodo({
      estado: $('#diario-estado').value,
      ...($('#diario-busqueda').value ? { q: $('#diario-busqueda').value } : {}),
    })}`);

    const cuerpo = $('#diario-tbody');
    if (!datos.asientos.length) {
      cuerpo.innerHTML = `
        <tr><td colspan="9" class="py-10 text-center text-surface-400">
          Todavía no hay asientos en este período.
          <p class="mt-1 text-xs">Usá «+ Nuevo asiento» o cargá los datos de prueba desde el Panel.</p>
        </td></tr>`;
    } else {
      cuerpo.innerHTML = datos.asientos.map((a) => `
        <tr>
          <td class="font-mono text-xs text-surface-600">${a.numero}</td>
          <td class="whitespace-nowrap text-xs">${fmtFecha(a.fecha)}</td>
          <td>
            <p class="font-medium text-surface-800">${esc(a.concepto)}</p>
            ${a.observaciones ? `<p class="text-xs text-surface-400">${esc(a.observaciones)}</p>` : ''}
          </td>
          <td class="text-center text-xs text-surface-500">${a.lineas}</td>
          <td class="num">${fmt(a.total_debe)}</td>
          <td class="num">${fmt(a.total_haber)}</td>
          <td class="text-center">
            ${UI.badge(a.cuadra ? 'Cuadra' : 'Descuadrado', {
              variant: a.cuadra ? 'success' : 'danger',
              iconName: a.cuadra ? 'cheque' : 'cruz',
            })}
          </td>
          <td class="text-center">${insigniaEstadoAsiento(a.estado)}</td>
          <td>
            <div class="flex justify-end gap-1.5">
              ${UI.botonHTML({
                label: 'Ver', size: 'xs', appearance: 'outline', variant: 'secondary',
                iconName: 'ojo', attributes: `data-ver="${a.id}"`,
              })}
              ${a.estado === 'BORRADOR' ? `
                ${UI.botonHTML({
                  label: 'Editar', size: 'xs', appearance: 'outline', variant: 'secondary',
                  iconName: 'lapiz', attributes: `data-editar="${a.id}"`,
                })}
                ${UI.botonHTML({
                  label: 'Confirmar', size: 'xs', appearance: 'outline', variant: 'success',
                  iconName: 'play', attributes: `data-confirmar="${a.id}"`,
                })}
                ${UI.botonHTML({
                  label: 'Borrar', size: 'xs', appearance: 'outline', variant: 'danger',
                  iconName: 'papelera', attributes: `data-borrar="${a.id}"`,
                })}` : ''}
              ${a.estado === 'CONFIRMADO' ? UI.botonHTML({
                label: 'Anular', size: 'xs', appearance: 'outline', variant: 'danger',
                iconName: 'prohibido', attributes: `data-anular="${a.id}"`,
              }) : ''}
            </div>
          </td>
        </tr>`).join('');
    }

    $('#diario-resumen').innerHTML = `
      <span>${datos.asientos.length} asiento(s) en el filtro</span>
      <span>Total Debe <strong class="font-mono">${fmt(datos.totales.debe)}</strong></span>
      <span>Total Haber <strong class="font-mono">${fmt(datos.totales.haber)}</strong></span>
      <span class="ml-auto">${datos.totales.debe === datos.totales.haber
        ? UI.badge('los totales del filtro cuadran', { variant: 'success', iconName: 'cheque' })
        : UI.badge('el filtro no cuadra (puede incluir borradores o anulados)', { variant: 'warning', iconName: 'alerta' })}</span>`;

    UI.delegar(cuerpo, '[data-ver]', (d) => verAsiento(d.ver));
    UI.delegar(cuerpo, '[data-editar]', (d) => editarAsiento(d.editar));
    UI.delegar(cuerpo, '[data-confirmar]', (d) => confirmarAsiento(d.confirmar));
    UI.delegar(cuerpo, '[data-anular]', (d) => anularAsiento(d.anular));
    UI.delegar(cuerpo, '[data-borrar]', (d) => borrarAsiento(d.borrar));
  } catch (error) {
    avisarError(error);
  }
}

function insigniaEstadoAsiento(estadoAsiento) {
  const mapa = {
    CONFIRMADO: { variant: 'success', icon: 'cheque' },
    BORRADOR: { variant: 'warning', icon: 'lapiz' },
    ANULADO: { variant: 'secondary', icon: 'prohibido' },
  };
  const config = mapa[estadoAsiento];
  if (!config) return esc(estadoAsiento);
  return UI.badge(estadoAsiento === 'BORRADOR' ? 'Borrador'
    : estadoAsiento === 'ANULADO' ? 'Anulado' : 'Confirmado',
    { variant: config.variant, iconName: config.icon });
}

// --- Formulario de asiento ------------------------------------------------

function iniciarFormulario(asiento = null) {
  estado.asientoEditando = asiento;
  estado.lineas = asiento
    ? asiento.partidas.map((p) => ({
        cuenta: p.cuenta, detalle: p.detalle || '',
        debe: (p.debe || 0) / 100, haber: (p.haber || 0) / 100,
      }))
    : [{ cuenta: '', detalle: '', debe: 0, haber: 0 },
       { cuenta: '', detalle: '', debe: 0, haber: 0 }];

  $('#diario-form-titulo').textContent = asiento
    ? `Editando asiento N.º ${asiento.numero}`
    : 'Nuevo asiento';
  $('#btn-cancelar-asiento').classList.toggle('oculto', !asiento);

  pintarCamposCabecera(asiento);
  pintarLineas();
}

function pintarCamposCabecera(asiento) {
  const formulario = $('#form-asiento');
  formulario.innerHTML = `
    <div class="grid gap-3 md:grid-cols-[170px_minmax(0,1fr)]">
      ${UI.campoHTML({
        id: 'asiento-fecha',
        label: 'Fecha',
        isRequired: true,
        control: UI.inputHTML({
          id: 'asiento-fecha', name: 'fecha', type: 'date',
          value: asiento?.fecha ?? new Date().toISOString().slice(0, 10),
        }),
      })}
      ${UI.campoHTML({
        id: 'asiento-concepto',
        label: 'Concepto',
        isRequired: true,
        control: UI.inputHTML({
          id: 'asiento-concepto', name: 'concepto', value: asiento?.concepto ?? '',
          placeholder: 'Ej.: Compra de mercaderías al contado',
        }),
      })}
    </div>

    ${UI.campoHTML({
      id: 'asiento-observaciones',
      label: 'Observaciones (opcional)',
      control: UI.inputHTML({
        id: 'asiento-observaciones', name: 'observaciones',
        value: asiento?.observaciones ?? '',
      }),
    })}

    <div>
      <p class="label">Partidas del asiento</p>
      <p class="help-text -mt-0.5 mb-2">
        Cargá al menos dos líneas. El control 1 exige que el total del Debe sea
        igual al total del Haber.
      </p>
      <div id="lineas-asiento" class="space-y-2"></div>
      ${UI.botonHTML({
        label: 'Agregar línea', variant: 'secondary', appearance: 'outline', size: 'sm',
        iconName: 'mas', attributes: 'id="btn-agregar-linea" class="mt-2"',
      })}
    </div>

    <div id="cuadre-asiento"></div>

    <div class="flex flex-wrap items-center gap-2 border-t border-surface-200 pt-4">
      ${UI.botonHTML({
        label: estado.asientoEditando ? 'Guardar cambios' : 'Confirmar y contabilizar',
        iconName: 'cheque', attributes: 'type="submit"',
      })}
      ${UI.botonHTML({
        label: 'Guardar como borrador', variant: 'secondary', appearance: 'outline',
        iconName: 'lapiz', attributes: 'id="btn-guardar-borrador"',
      })}
      <p class="text-xs text-surface-500">
        Un asiento descuadrado o con cuentas inactivas se puede guardar como
        borrador, pero no se contabiliza.
      </p>
    </div>
  `;

  $('#btn-agregar-linea').onclick = agregarLinea;
  $('#btn-guardar-borrador').onclick = (evento) => guardarAsiento(evento, false);
  formulario.onsubmit = (evento) => guardarAsiento(evento, true);
  pintarLineas();
}

function pintarLineas() {
  const contenedor = $('#lineas-asiento');
  contenedor.innerHTML = estado.lineas.map((linea, indice) => `
    <div class="grid grid-cols-12 items-end gap-2 rounded-field border border-surface-200
                bg-surface-50/50 p-3" data-indice="${indice}">
      <div class="col-span-12 mb-1 flex items-center justify-between sm:col-span-11">
        <span class="text-[11px] font-semibold text-surface-500">Línea ${indice + 1}</span>
        ${estado.lineas.length > 2
          ? `<button type="button" class="text-xs font-medium text-surface-400 transition
                                     hover:text-danger-600" data-quitar="${indice}">Quitar</button>`
          : ''}
      </div>
      <div class="col-span-12 sm:col-span-4">
        <label class="label" for="linea-${indice}-cuenta">Cuenta</label>
        <select id="linea-${indice}-cuenta" class="select" data-campo="cuenta" data-indice="${indice}">
          <option value="">Elegir cuenta…</option>
          ${opcionesCuentas(linea.cuenta)}
        </select>
      </div>
      <div class="col-span-12 sm:col-span-4">
        <label class="label" for="linea-${indice}-detalle">Detalle</label>
        <input id="linea-${indice}-detalle" class="input" data-campo="detalle" data-indice="${indice}"
               placeholder="Detalle de la línea" value="${esc(linea.detalle)}">
      </div>
      <div class="col-span-6 sm:col-span-2">
        <label class="label text-right" for="linea-${indice}-debe">Debe</label>
        <input id="linea-${indice}-debe" class="input text-right font-mono tabular-nums"
               data-campo="debe" data-indice="${indice}" inputmode="decimal"
               placeholder="0,00" value="${linea.debe ? linea.debe : ''}">
      </div>
      <div class="col-span-6 sm:col-span-2">
        <label class="label text-right" for="linea-${indice}-haber">Haber</label>
        <input id="linea-${indice}-haber" class="input text-right font-mono tabular-nums"
               data-campo="haber" data-indice="${indice}" inputmode="decimal"
               placeholder="0,00" value="${linea.haber ? linea.haber : ''}">
      </div>
    </div>`).join('');

  $$('#lineas-asiento [data-campo]').forEach((campo) => {
    const indice = Number(campo.dataset.indice);
    const clave = campo.dataset.campo;
    const esImporte = clave === 'debe' || clave === 'haber';

    // 'input' cubre la escritura; 'change' cubre el <select> y el pegado final.
    const sincronizar = () => {
      estado.lineas[indice][clave] = esImporte ? leerMonto(campo.value) : campo.value;
      if (esImporte) calcularCuadre();
    };

    campo.addEventListener('input', sincronizar);
    campo.addEventListener('change', () => {
      sincronizar();
      if (esImporte) {
        // Al salir del campo se normaliza el importe para que se lea mejor.
        escribirMonto(campo, Math.round(estado.lineas[indice][clave] * 100));
        calcularCuadre();
      }
    });
  });

  UI.delegar(contenedor, '[data-quitar]', (d) => {
    estado.lineas.splice(Number(d.quitar), 1);
    pintarLineas();
  });

  calcularCuadre();
}

function agregarLinea() {
  estado.lineas.push({ cuenta: '', detalle: '', debe: 0, haber: 0 });
  pintarLineas();
}

function calcularCuadre() {
  const totales = totalesAsiento();
  const cuadra = totales.debe === totales.haber && totales.debe > 0;
  const diferencia = totales.debe - totales.haber;
  const incompletas = estado.lineas.filter(
    (l) => !l.cuenta || (l.debe === 0 && l.haber === 0)
  ).length;

  $('#cuadre-asiento').innerHTML = `
    <div class="alert ${cuadra ? 'alert-success' : 'alert-warning'} block">
      <div class="flex flex-wrap items-center gap-x-6 gap-y-1">
        <span class="font-medium">${UI.icono(cuadra ? 'cheque' : 'alerta', { tamano: 16 })}
          ${cuadra ? 'El asiento cuadra' : 'El asiento todavía no cuadra'}</span>
        <span>Total Debe: <strong class="font-mono tabular-nums">${fmt(totales.debe)}</strong></span>
        <span>Total Haber: <strong class="font-mono tabular-nums">${fmt(totales.haber)}</strong></span>
        <span>Diferencia: <strong class="font-mono tabular-nums">${fmt(diferencia)}</strong></span>
      </div>
      ${incompletas
        ? `<p class="mt-1 text-xs opacity-80">${incompletas} línea(s) sin cuenta o sin importe.</p>`
        : ''}
    </div>`;
}

function totalesAsiento() {
  return estado.lineas.reduce((acumulado, linea) => ({
    debe: acumulado.debe + Math.round((linea.debe || 0) * 100),
    haber: acumulado.haber + Math.round((linea.haber || 0) * 100),
  }), { debe: 0, haber: 0 });
}

async function guardarAsiento(evento, confirmar) {
  evento?.preventDefault();

  const cabecera = {
    fecha: $('#asiento-fecha').value,
    concepto: $('#asiento-concepto').value,
    observaciones: $('#asiento-observaciones').value,
  };

  const partidas = estado.lineas
    .filter((l) => l.cuenta && (l.debe || l.haber))
    .map((l) => ({
      cuenta: l.cuenta,
      detalle: l.detalle,
      // Se envían los centavos como números enteros.
      debe: Math.round((l.debe || 0) * 100),
      haber: Math.round((l.haber || 0) * 100),
    }));

  if (!partidas.length) {
    avisar('Cargá al menos una línea con cuenta e importe.', 'warning', 'Asiento incompleto');
    return;
  }

  try {
    let respuesta;
    if (estado.asientoEditando) {
      respuesta = await api(`/api/asientos/${estado.asientoEditando}`, {
        method: 'PUT', body: { ...cabecera, partidas },
      });
    } else {
      respuesta = await api('/api/asientos', {
        method: 'POST', body: { ...cabecera, partidas, confirmar },
      });
    }

    const contabilizado = respuesta.asiento.estado === 'CONFIRMADO';
    avisar(
      `Asiento N.º ${respuesta.asiento.numero} ${contabilizado ? 'contabilizado' : 'guardado como borrador'}.`,
      contabilizado ? 'success' : 'info',
      contabilizado ? 'Asiento contabilizado' : 'Borrador guardado',
      contabilizado ? [] : ['Un borrador no suma en el Libro Mayor ni en el Balance hasta confirmarlo.'],
    );
    iniciarFormulario();
    await Promise.all([cargarDiario(), cargarPanel()]);
  } catch (error) {
    avisarError(error);
  }
}

async function verAsiento(id) {
  try {
    const { asiento } = await api(`/api/asientos/${id}`);
    const contenedor = $('#diario-detalle');
    contenedor.classList.remove('oculto');

    contenedor.innerHTML = `
      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Asiento N.º ${asiento.numero} · ${fmtFecha(asiento.fecha)}</h2>
          ${insigniaEstadoAsiento(asiento.estado)}
          <div class="ml-auto">
            ${UI.botonHTML({
              label: 'Cerrar', variant: 'secondary', appearance: 'outline', size: 'sm',
              iconName: 'equis', attributes: 'id="btn-cerrar-detalle"',
            })}
          </div>
        </div>
        <div class="card-body">
          <p class="text-sm font-medium text-surface-800">${esc(asiento.concepto)}</p>
          ${asiento.observaciones
            ? `<p class="mt-0.5 text-xs text-surface-500">${esc(asiento.observaciones)}</p>` : ''}
          <div class="table-shell mt-3 rounded-field border border-surface-200">
            <table class="table">
              <thead>
                <tr>
                  <th class="w-12">Línea</th><th class="w-24">Código</th><th>Cuenta</th>
                  <th>Detalle</th><th class="w-32 text-right">Debe</th><th class="w-32 text-right">Haber</th>
                </tr>
              </thead>
              <tbody>
                ${asiento.partidas.map((p) => `
                  <tr>
                    <td class="text-xs text-surface-400">${p.linea}</td>
                    <td class="font-mono text-xs">${esc(p.cuenta)}</td>
                    <td>
                      ${esc(p.cuenta_nombre)}
                      ${UI.badge(p.cuenta_naturaleza === 'D' ? 'D' : 'A', {
                        variant: p.cuenta_naturaleza === 'D' ? 'primary' : 'secondary',
                        className: 'ml-1',
                      })}
                    </td>
                    <td class="text-xs text-surface-500">${esc(p.detalle ?? '')}</td>
                    <td class="num">${p.debe ? fmt(p.debe) : ''}</td>
                    <td class="num">${p.haber ? fmt(p.haber) : ''}</td>
                  </tr>`).join('')}
              </tbody>
              <tfoot>
                <tr>
                  <td colspan="4">
                    Totales
                    ${asiento.cuadra
                      ? UI.badge('cuadra', { variant: 'success', iconName: 'cheque', className: 'ml-1' })
                      : UI.badge('descuadrado', { variant: 'danger', iconName: 'cruz', className: 'ml-1' })}
                  </td>
                  <td class="num">${fmt(asiento.total_debe)}</td>
                  <td class="num">${fmt(asiento.total_haber)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      </div>`;

    $('#btn-cerrar-detalle').onclick = () => contenedor.classList.add('oculto');
    contenedor.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    avisarError(error);
  }
}

async function editarAsiento(id) {
  try {
    const { asiento } = await api(`/api/asientos/${id}`);
    $('#diario-detalle').classList.add('oculto');
    iniciarFormulario(asiento);
    $('#form-asiento').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    avisarError(error);
  }
}

async function confirmarAsiento(id) {
  try {
    const { asiento } = await api(`/api/asientos/${id}/confirmar`, { method: 'POST' });
    avisar(`Asiento N.º ${asiento.numero} contabilizado.`, 'success');
    await Promise.all([cargarDiario(), cargarPanel()]);
  } catch (error) {
    avisarError(error);
  }
}

async function anularAsiento(id) {
  // Un asiento confirmado no se edita ni se borra: se anula con un motivo,
  // que queda registrado en el Diario.
  const motivo = await UI.preguntar({
    titulo: 'Anular el asiento',
    descripcion: 'El asiento queda registrado pero deja de sumar en el Libro Mayor '
      + 'y en el Balance. El motivo queda guardado.',
    etiqueta: 'Motivo de la anulación',
    placeholder: 'Ej.: Error de tipeo en el importe',
    multilinea: true,
    requerido: true,
    confirmarTexto: 'Anular asiento',
    icono: 'prohibido',
  });
  if (motivo === null) return;

  try {
    await api(`/api/asientos/${id}/anular`, { method: 'POST', body: { motivo } });
    avisar('Asiento anulado. Ya no suma en el Libro Mayor ni en el Balance.', 'success');
    await Promise.all([cargarDiario(), cargarPanel()]);
  } catch (error) {
    avisarError(error);
  }
}

async function borrarAsiento(id) {
  const aceptado = await UI.confirmar({
    titulo: 'Eliminar el asiento',
    descripcion: 'Se borra definitivamente. Solo se puede hacer con asientos en estado '
      + 'borrador: un asiento confirmado se anula, no se borra.',
    confirmarTexto: 'Eliminar',
    icono: 'papelera',
  });
  if (!aceptado) return;

  try {
    await api(`/api/asientos/${id}`, { method: 'DELETE' });
    avisar('Asiento eliminado.', 'success');
    await Promise.all([cargarDiario(), cargarPanel()]);
  } catch (error) {
    avisarError(error);
  }
}

// ============================================================================
// Vista: Libro Mayor
// ============================================================================

function actualizarSelectoresCuenta() {
  const select = $('#mayor-cuenta');
  if (!select) return;
  const actual = select.value;
  select.innerHTML = '<option value="">Elegir cuenta…</option>' + opcionesCuentas();
  if (actual) select.value = actual;
}

async function cargarMayor(todas = false) {
  if (!todas && !$('#mayor-cuenta').value) {
    $('#mayor-resultado').innerHTML = `
      <div class="card">
        <div class="card-body py-12 text-center text-sm text-surface-400">
          ${UI.icono('balanza', { tamano: 28, clase: 'mx-auto mb-2 text-surface-300' })}
          Elegí una cuenta para ver su movimiento, o usá «Todas las cuentas».
        </div>
      </div>`;
    $('#mayor-cabecera').innerHTML = '';
    return;
  }

  try {
    if (todas) {
      const datos = await api(`/api/libro-mayor${conPeriodo()}`);
      $('#mayor-cabecera').innerHTML = `
        <p class="text-sm text-surface-600">
          Libro Mayor completo · ${datos.cuentas.length} cuenta(s) con movimiento.
        </p>
        <p class="mt-0.5 text-xs text-surface-400">
          Origen: ${esc(datos.origen)} (control ${datos.regla_aplicada}).
          ${UI.badge('Calculado, no cargado', { variant: 'success', className: 'ml-1' })}
        </p>`;
      $('#mayor-resultado').innerHTML = pintarMayorCompleto(datos);
    } else {
      const codigo = $('#mayor-cuenta').value;
      const datos = await api(`/api/cuentas/${encodeURIComponent(codigo)}/mayor${conPeriodo()}`);
      $('#mayor-cabecera').innerHTML = `
        <div class="flex flex-wrap items-center gap-2 pb-3">
          <p class="text-sm text-surface-600">
            Cuenta <strong>${esc(datos.cuenta.codigo)} · ${esc(datos.cuenta.nombre)}</strong>
          </p>
          ${badgeNaturaleza(datos.cuenta.naturaleza, datos.cuenta.naturaleza_nombre)}
          ${UI.badge(datos.cuenta.clasificacion_nombre, { variant: 'secondary' })}
        </div>`;
      $('#mayor-resultado').innerHTML = pintarMayorCuenta(datos);
    }
  } catch (error) {
    avisarError(error);
  }
}

function pintarMayorCuenta(datos) {
  const sinMovimientos = !datos.movimientos.length;
  return `
    <div class="card">
      <div class="table-shell">
        <table class="table">
          <thead>
            <tr>
              <th class="w-28">Fecha</th><th class="w-16">N.º</th>
              <th>Concepto</th><th>Detalle</th>
              <th class="w-32 text-right">Debe</th><th class="w-32 text-right">Haber</th>
              <th class="w-32 text-right">Saldo</th>
            </tr>
          </thead>
          <tbody>
            ${datos.saldo_anterior ? `
              <tr class="bg-surface-50">
                <td colspan="6" class="font-medium text-surface-600">Saldo anterior al período</td>
                <td class="num font-medium">${fmt(datos.saldo_anterior)}</td>
              </tr>` : ''}
            ${sinMovimientos ? `
              <tr><td colspan="7" class="py-10 text-center text-surface-400">
                La cuenta no tiene movimientos confirmados en el período seleccionado.
              </td></tr>` : ''}
            ${datos.movimientos.map((m) => `
              <tr>
                <td class="whitespace-nowrap text-xs">${fmtFecha(m.fecha)}</td>
                <td class="font-mono text-xs text-surface-600">${m.numero}</td>
                <td class="text-sm">${esc(m.concepto)}</td>
                <td class="text-xs text-surface-500">${esc(m.detalle ?? '')}</td>
                <td class="num">${m.debe ? fmt(m.debe) : ''}</td>
                <td class="num">${m.haber ? fmt(m.haber) : ''}</td>
                <td class="num font-semibold">${fmt(m.saldo)}</td>
              </tr>`).join('')}
          </tbody>
          <tfoot>
            <tr>
              <td colspan="4">Totales de la cuenta</td>
              <td class="num">${fmt(datos.totales.debe)}</td>
              <td class="num">${fmt(datos.totales.haber)}</td>
              <td class="num">${fmt(datos.totales.saldo_final)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>`;
}

function pintarMayorCompleto(datos) {
  if (!datos.cuentas.length) {
    return `
      <div class="card">
        <div class="card-body py-12 text-center text-sm text-surface-400">
          Todavía no hay asientos confirmados, así que el Libro Mayor está vacío.
        </div>
      </div>`;
  }

  return datos.cuentas.map((bloque) => `
    <div class="card mb-4">
      <div class="card-header">
        <h2 class="card-title">
          <span class="font-mono text-xs text-surface-500">${esc(bloque.cuenta.codigo)}</span>
          ${esc(bloque.cuenta.nombre)}
        </h2>
        ${badgeNaturaleza(bloque.cuenta.naturaleza, bloque.cuenta.naturaleza_nombre)}
        <span class="ml-auto text-xs text-surface-500">
          ${bloque.movimientos.length} movimiento(s) ·
          Saldo final <strong class="font-mono">${fmt(bloque.totales.saldo_final)}</strong>
        </span>
      </div>
      <div class="table-shell">
        <table class="table">
          <thead>
            <tr>
              <th class="w-28">Fecha</th><th class="w-16">N.º</th>
              <th>Concepto</th><th class="w-32 text-right">Debe</th>
              <th class="w-32 text-right">Haber</th><th class="w-32 text-right">Saldo</th>
            </tr>
          </thead>
          <tbody>
            ${bloque.saldo_anterior ? `
              <tr class="bg-surface-50">
                <td colspan="5" class="text-xs text-surface-500">Saldo anterior</td>
                <td class="num text-xs">${fmt(bloque.saldo_anterior)}</td>
              </tr>` : ''}
            ${bloque.movimientos.map((m) => `
              <tr>
                <td class="whitespace-nowrap text-xs">${fmtFecha(m.fecha)}</td>
                <td class="font-mono text-xs text-surface-600">${m.numero}</td>
                <td class="text-sm">${esc(m.concepto)}</td>
                <td class="num">${m.debe ? fmt(m.debe) : ''}</td>
                <td class="num">${m.haber ? fmt(m.haber) : ''}</td>
                <td class="num font-semibold">${fmt(m.saldo)}</td>
              </tr>`).join('')}
          </tbody>
          <tfoot>
            <tr>
              <td colspan="3">Totales</td>
              <td class="num">${fmt(bloque.totales.debe)}</td>
              <td class="num">${fmt(bloque.totales.haber)}</td>
              <td class="num">${fmt(bloque.totales.saldo_final)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>`).join('')
    + `
    <div class="card mb-4">
      <div class="card-body">
        <p class="text-sm text-surface-700">
          <strong>Total general del Libro Mayor:</strong>
          Debe <span class="font-mono">${fmt(datos.totales.debe)}</span> ·
          Haber <span class="font-mono">${fmt(datos.totales.haber)}</span>
          ${UI.badge(datos.totales.cuadra ? 'cuadra' : 'no cuadra', {
            variant: datos.totales.cuadra ? 'success' : 'danger',
            iconName: datos.totales.cuadra ? 'cheque' : 'cruz',
            className: 'ml-1',
          })}
        </p>
      </div>
    </div>`;
}

// ============================================================================
// Vista: Balance de comprobación
// ============================================================================

async function cargarBalance() {
  try {
    const datos = await api(`/api/balance${conPeriodo({
      vacias: $('#balance-vacias').checked ? 1 : 0,
      agrupado: $('#balance-agrupado').checked ? 1 : 0,
    })}`);

    $('#balance-cabecera').innerHTML = `
      <div class="flex flex-wrap items-center gap-2 pb-3">
        ${UI.badge(datos.cuadrado ? 'Balance cuadrado' : 'Balance descuadrado', {
          variant: datos.cuadrado ? 'success' : 'danger',
          iconName: datos.cuadrado ? 'cheque' : 'cruz',
        })}
        <span class="text-xs text-surface-500">${esc(datos.origen)}</span>
      </div>`;

    $('#balance-resultado').innerHTML = `
      <div class="card mb-4">
        <div class="table-shell">
          <table class="table">
            <thead>
              <tr>
                <th class="w-24">Código</th><th>Cuenta</th><th class="w-40">Clasificación</th>
                <th class="w-24">Naturaleza</th>
                <th class="w-32 text-right">Débitos</th><th class="w-32 text-right">Créditos</th>
                <th class="w-32 text-right">Saldo Deudor</th><th class="w-32 text-right">Saldo Acreedor</th>
              </tr>
            </thead>
            <tbody>
              ${datos.cuentas.length ? datos.cuentas.map((c) => `
                <tr>
                  <td class="font-mono text-xs text-surface-600">${esc(c.codigo)}</td>
                  <td class="${c.estado === 'INACTIVA' ? 'text-surface-400' : ''}">
                    ${esc(c.nombre)}
                    ${c.estado === 'INACTIVA'
                      ? UI.badge('Inactiva', { variant: 'secondary', className: 'ml-1' }) : ''}
                  </td>
                  <td class="text-xs text-surface-500">${c.clasificacion} · ${esc(c.clasificacion_nombre)}</td>
                  <td class="text-xs">${badgeNaturaleza(c.naturaleza, c.naturaleza_nombre)}</td>
                  <td class="num">${c.debe ? fmt(c.debe) : ''}</td>
                  <td class="num">${c.haber ? fmt(c.haber) : ''}</td>
                  <td class="num font-semibold">${c.saldo_deudor ? fmt(c.saldo_deudor) : ''}</td>
                  <td class="num font-semibold">${c.saldo_acreedor ? fmt(c.saldo_acreedor) : ''}</td>
                </tr>`).join('')
                : `<tr><td colspan="8" class="py-10 text-center text-surface-400">
                     No hay cuentas con movimientos en el período seleccionado.
                   </td></tr>`}
            </tbody>
            <tfoot>
              <tr>
                <td colspan="4">TOTALES</td>
                <td class="num">${fmt(datos.totales.debe)}</td>
                <td class="num">${fmt(datos.totales.haber)}</td>
                <td class="num">${fmt(datos.totales.saldo_deudor)}</td>
                <td class="num">${fmt(datos.totales.saldo_acreedor)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>

      <div class="grid gap-4 lg:grid-cols-2">
        <div class="card">
          <div class="card-header"><h2 class="card-title">Verificaciones</h2></div>
          <div class="card-body space-y-2.5">
            ${Object.entries(datos.verificaciones).map(([clave, v]) => `
              <div class="flex items-start gap-2.5">
                ${UI.badge(v.cumple ? 'OK' : 'Falla', {
                  variant: v.cumple ? 'success' : 'danger',
                  iconName: v.cumple ? 'cheque' : 'cruz',
                })}
                <p class="text-sm ${v.cumple ? 'text-surface-700' : 'text-danger-700'}">${esc(v.mensaje)}</p>
              </div>`).join('')}
            <div class="flex items-start gap-2.5">
              ${UI.badge(datos.cruce_libro_mayor.coincide ? 'OK' : 'Falla', {
                variant: datos.cruce_libro_mayor.coincide ? 'success' : 'danger',
                iconName: datos.cruce_libro_mayor.coincide ? 'cheque' : 'cruz',
              })}
              <p class="text-sm text-surface-700">
                ${esc(datos.cruce_libro_mayor.mensaje)} (control 4 y 5)
              </p>
            </div>
          </div>
        </div>

        ${datos.por_clasificacion ? `
          <div class="card">
            <div class="card-header">
              <h2 class="card-title">Totales por clasificación</h2>
              ${UI.badge('Control 6', { variant: 'primary', className: 'ml-auto' })}
            </div>
            <div class="table-shell">
              <table class="table">
                <thead>
                  <tr>
                    <th>Clasificación</th><th class="w-28">Naturaleza</th>
                    <th class="w-20 text-right">Cuentas</th>
                    <th class="w-28 text-right">Débitos</th>
                    <th class="w-28 text-right">Créditos</th>
                    <th class="w-32 text-right">Saldo</th>
                  </tr>
                </thead>
                <tbody>
                  ${datos.por_clasificacion.filter((g) => g.cuentas).map((g) => `
                    <tr>
                      <td class="font-medium">${esc(g.nombre)}</td>
                      <td>${badgeNaturaleza(g.naturaleza, g.naturaleza_nombre)}</td>
                      <td class="num">${g.cuentas}</td>
                      <td class="num">${fmt(g.debe, false)}</td>
                      <td class="num">${fmt(g.haber, false)}</td>
                      <td class="num font-semibold">${fmt(g.saldo_natural, false)}</td>
                    </tr>`).join('')}
                </tbody>
              </table>
            </div>
          </div>` : ''}
      </div>`;
  } catch (error) {
    avisarError(error);
  }
}

// ============================================================================
// Vista: Controles y reglas
// ============================================================================

async function cargarControles() {
  try {
    const datos = await api('/api/controles');

    $('#controles-tbody').innerHTML = datos.controles.map((c) => `
      <tr>
        <td>
          ${UI.badge(c.codigo, {
            variant: c.cumple ? 'success' : 'danger',
            iconName: c.cumple ? 'cheque' : 'cruz',
          })}
        </td>
        <td class="font-medium text-surface-800">${esc(c.titulo)}</td>
        <td class="text-sm text-surface-600">${esc(c.requisito)}</td>
        <td class="text-xs text-surface-500">${esc(c.implementacion)}</td>
        <td class="text-xs text-surface-500">${esc(c.detalle)}</td>
      </tr>`).join('');

    $('#trazabilidad-tbody').innerHTML = datos.trazabilidad.map((t) => `
      <tr>
        <td class="font-medium text-surface-800">${esc(t.reporte)}</td>
        <td class="font-mono text-xs">${t.tablas.map((x) => esc(x)).join(' + ')}</td>
        <td class="text-sm text-surface-600">${esc(t.descripcion)}</td>
        <td class="text-center">
          ${t.carga_manual
            ? UI.badge('Sí', { variant: 'warning', iconName: 'alerta' })
            : UI.badge('Imposible', { variant: 'success', iconName: 'candado' })}
        </td>
      </tr>`).join('');
  } catch (error) {
    avisarError(error);
  }
}

// ============================================================================
// Vista: IVA de Paraguay (Ley N.º 1259/97)
// ============================================================================

/**
 * Prepara la vista de IVA: catálogo de alícuotas, cuentas de los selects y
 * el desglose inicial. Se ejecuta cada vez que se entra a la pestaña.
 */
async function cargarIVA() {
  try {
    if (!estado.ivaListo) {
      const cfg = await api('/api/iva/alicuotas');
      estado.iva = cfg;

      // El selector de alícuotas se arma con el catálogo del servidor: así la
      // interfaz y el backend nunca se separan (10 %, 5 % y exenta).
      $('#iva-alicuota').innerHTML = cfg.alicuotas.map((a) => `
        <option value="${a.valor}">${esc(a.etiqueta)}</option>`).join('');

      $('#iva-estado').innerHTML = UI.badge('Ley N.º 1259/97', {
        variant: 'primary', iconName: 'balanza',
      });
      estado.ivaListo = true;
    }

    // Las cuentas de cobro/pago y de gasto salen del plan cargado.
    const contrapartida = $('#iva-cuenta');
    if (!contrapartida.options || !contrapartida.options.length) {
      contrapartida.innerHTML = opcionesCuentas('1.1.01');
      $('#iva-cuenta-gasto').innerHTML = opcionesCuentas('1.1.04');
    }

    // El período de la liquidación arranca con el del ejercicio.
    if (!$('#iva-desde').value) $('#iva-desde').value = estado.periodo.desde || '';
    if (!$('#iva-hasta').value) $('#iva-hasta').value = estado.periodo.hasta || '';
    if (!$('#iva-fecha').value) $('#iva-fecha').value = hoy();

    conectarFormularioIVA();
    actualizarDesgloseIVA();
  } catch (error) {
    avisarError(error);
  }
}

/** Fecha de hoy en formato ISO (AAAA-MM-DD), para los campos de fecha. */
function hoy() {
  const d = new Date();
  const dos = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())}`;
}

/** Conecta los eventos del formulario de IVA (una sola vez por carga). */
function conectarFormularioIVA() {
  const recalcular = () => actualizarDesgloseIVA();

  $('#iva-importe').oninput = recalcular;
  $('#iva-alicuota').onchange = recalcular;
  $('#iva-modo').onchange = recalcular;
  $('#iva-tipo').onchange = () => {
    // La cuenta de gasto solo tiene sentido en las compras.
    const esVenta = $('#iva-tipo').value === 'venta';
    $('#iva-campo-gasto').classList.toggle('hidden', esVenta);
    recalcular();
  };
  $('#iva-campo-gasto').classList.toggle('hidden', $('#iva-tipo').value === 'venta');

  $('#btn-iva-contabilizar').onclick = contabilizarIVA;
  $('#btn-iva-resumen').onclick = verResumenIVA;
  $('#btn-iva-liquidar').onclick = liquidarIVA;
}

/** Muestra el desglose base + IVA + total mientras se escribe el importe. */
async function actualizarDesgloseIVA() {
  const caja = $('#iva-desglose');
  const importe = leerMonto($('#iva-importe').value);
  const alicuota = Number($('#iva-alicuota').value);
  const porTotal = $('#iva-modo').value === 'total';

  if (!importe) {
    caja.innerHTML = `<p class="text-surface-500">
      Escribí un importe para ver el desglose del impuesto.</p>`;
    return;
  }

  try {
    const cuerpo = porTotal
      ? { total: importe, alicuota }
      : { base: importe, alicuota };
    const d = await api('/api/iva/calcular', { method: 'POST', body: cuerpo });

    caja.innerHTML = `
      <dl class="grid grid-cols-2 gap-y-1.5">
        <dt class="text-surface-500">Base imponible</dt>
        <dd class="num text-right font-medium">${esc(d.base_formateado)}</dd>
        <dt class="text-surface-500">IVA ${d.alicuota}%</dt>
        <dd class="num text-right font-medium text-primary-600">${esc(d.iva_formateado)}</dd>
        <dt class="border-t border-surface-200 pt-1.5 font-semibold">Total</dt>
        <dd class="num border-t border-surface-200 pt-1.5 text-right font-semibold">
          ${esc(d.total_formateado)}
        </dd>
      </dl>
      <p class="mt-2 text-xs text-surface-500">
        Cuentas: ${esc(d.alicuota === 0
          ? 'Ventas Exentas · no se genera línea de IVA'
          : porTotal
            ? 'el impuesto se discrimina del total (el precio ya lo incluye)'
            : 'el impuesto se suma a la base')}
      </p>`;
  } catch (error) {
    caja.innerHTML = `<p class="text-danger-600">${esc(error.mensaje)}</p>`;
  }
}

/** Contabiliza el asiento de venta o compra con IVA. */
async function contabilizarIVA() {
  const tipo = $('#iva-tipo').value;
  const importe = leerMonto($('#iva-importe').value);
  if (!importe) {
    avisar('Escribí el importe de la operación.', 'warning', 'Falta el importe');
    return;
  }

  const porTotal = $('#iva-modo').value === 'total';
  const cuerpo = {
    tipo,
    fecha: $('#iva-fecha').value,
    concepto: $('#iva-concepto').value || (tipo === 'venta'
      ? 'Venta con IVA' : 'Compra con IVA'),
    cuenta: $('#iva-cuenta').value,
    alicuota: Number($('#iva-alicuota').value),
    ...(porTotal ? { total: importe } : { base: importe }),
  };
  if (tipo === 'compra') cuerpo.cuenta_gasto = $('#iva-cuenta-gasto').value;

  const boton = $('#btn-iva-contabilizar');
  boton.disabled = true;
  boton.classList.add('btn-pending');

  try {
    const r = await api('/api/iva/asiento', { method: 'POST', body: cuerpo });
    avisar(
      `Asiento N.º ${r.asiento.numero} contabilizado por ${r.calculo.total_formateado}.`,
      'success', 'Operación registrada',
    );
    // La tabla del Diario y el Balance quedan desactualizados: se recargan al
    // entrar a esas vistas, y acá se limpia el formulario.
    $('#iva-importe').value = '';
    $('#iva-concepto').value = '';
    $('#iva-resultado').innerHTML = pintarAsientoIVA(r);
    actualizarDesgloseIVA();
  } catch (error) {
    avisarError(error);
  } finally {
    boton.disabled = false;
    boton.classList.remove('btn-pending');
  }
}

/** Tabla con las partidas del asiento recién generado. */
function pintarAsientoIVA(r) {
  const partidas = (r.asiento.partidas || []);
  return `
    <div class="card">
      <div class="card-header">
        <div>
          <h3 class="card-title">Asiento N.º ${r.asiento.numero} ·
            ${fmtFecha(r.asiento.fecha)}</h3>
          <p class="card-subtitle">${esc(r.asiento.concepto)}</p>
        </div>
        ${UI.badge(r.asiento.estado, {
          variant: r.asiento.estado === 'CONFIRMADO' ? 'success' : 'secondary',
          iconName: r.asiento.estado === 'CONFIRMADO' ? 'cheque' : 'lapiz',
        })}
      </div>
      <div class="table-shell">
        <table class="table">
          <thead><tr>
            <th class="w-24">Cuenta</th><th>Detalle</th>
            <th class="w-32 text-right">Debe</th>
            <th class="w-32 text-right">Haber</th>
          </tr></thead>
          <tbody>
            ${partidas.map((p) => `
              <tr>
                <td class="font-mono text-xs text-surface-600">${esc(p.cuenta)}</td>
                <td>${esc(p.detalle || '')}</td>
                <td class="num">${p.debe ? fmt(p.debe) : ''}</td>
                <td class="num">${p.haber ? fmt(p.haber) : ''}</td>
              </tr>`).join('')}
          </tbody>
          <tfoot><tr>
            <td colspan="2">TOTALES</td>
            <td class="num">${fmt(r.asiento.total_debe)}</td>
            <td class="num">${fmt(r.asiento.total_haber)}</td>
          </tr></tfoot>
        </table>
      </div>
    </div>`;
}

/** Consulta el Débito y el Crédito Fiscal acumulados en el período. */
async function verResumenIVA() {
  try {
    const d = await api(`/api/iva/resumen${conRangoIVA()}`);
    const liq = d.liquidacion;

    const etiqueta = {
      A_PAGAR: ['Saldo a pagar al fisco', 'warning'],
      A_RECUPERAR: ['Saldo a favor del contribuyente', 'success'],
      SIN_SALDO: ['El período cierra exacto', 'secondary'],
    }[liq.situacion] || ['Sin datos', 'secondary'];

    $('#iva-liquidacion').innerHTML = `
      <div class="rounded-lg border border-surface-200 bg-surface-50 p-4">
        <dl class="grid grid-cols-2 gap-y-1.5 text-sm">
          <dt class="text-surface-500">Débito Fiscal (ventas)</dt>
          <dd class="num text-right font-medium">${esc(d.debito_formateado)}</dd>
          <dt class="text-surface-500">Crédito Fiscal (compras)</dt>
          <dd class="num text-right font-medium">${esc(d.credito_formateado)}</dd>
          <dt class="border-t border-surface-200 pt-1.5 font-semibold">
            ${liq.situacion === 'A_RECUPERAR' ? 'A recuperar' : 'A pagar'}
          </dt>
          <dd class="num border-t border-surface-200 pt-1.5 text-right font-semibold">
            ${esc(liq.monto_formateado)}
          </dd>
        </dl>
        <div class="mt-3">
          ${UI.badge(etiqueta[0], { variant: etiqueta[1] })}
        </div>
      </div>`;
  } catch (error) {
    avisarError(error);
  }
}

/** Arma el ?desde=&hasta= con los campos de fecha de la liquidación. */
function conRangoIVA() {
  const params = new URLSearchParams();
  if ($('#iva-desde').value) params.set('desde', $('#iva-desde').value);
  if ($('#iva-hasta').value) params.set('hasta', $('#iva-hasta').value);
  const texto = params.toString();
  return texto ? `?${texto}` : '';
}

/** Contabiliza la liquidación del IVA del período. */
async function liquidarIVA() {
  const boton = $('#btn-iva-liquidar');
  boton.disabled = true;
  boton.classList.add('btn-pending');

  try {
    const r = await api('/api/iva/liquidar', {
      method: 'POST',
      body: {
        desde: $('#iva-desde').value || undefined,
        hasta: $('#iva-hasta').value || undefined,
        fecha: hoy(),
      },
    });

    if (!r.contabilizado) {
      avisar(r.motivo, 'info', 'Nada que liquidar');
    } else {
      avisar(
        `Asiento N.º ${r.asiento.numero}: ${r.liquidacion.monto_formateado} ` +
        `(${r.liquidacion.cuenta_saldo}).`,
        'success', 'Liquidación contabilizada',
      );
    }
    await verResumenIVA();
  } catch (error) {
    avisarError(error);
  } finally {
    boton.disabled = false;
    boton.classList.remove('btn-pending');
  }
}

// ============================================================================
// Vista: Asistente virtual (Groq con respaldo en Gemini)
// ============================================================================

/** Evita envíos superpuestos mientras el proveedor responde. */
let asistenteEnviando = false;

/**
 * Consulta la configuración de los proveedores y deja el formulario listo.
 * Se hace una sola vez: la interfaz muestra después si Groq y Gemini están
 * activos, para que el usuario sepa por qué una respuesta puede demorar.
 */
async function cargarAsistente() {
  if (!estado.asistenteListo) {
    try {
      pintarEstadoAsistente(await api('/api/asistente/configuracion'));
      estado.asistenteListo = true;
    } catch (error) {
      avisarError(error);
      return;
    }
  }

  $('#asistente-formulario').onsubmit = (evento) => {
    evento.preventDefault();
    enviarPregunta();
  };
}

function pintarEstadoAsistente(cfg) {
  const contenedor = $('#asistente-estado');
  if (!contenedor) return;

  if (!cfg.configurado) {
    contenedor.innerHTML = UI.badge('sin claves de API', {
      variant: 'warning', iconName: 'alerta',
    });
    return;
  }
  const activos = cfg.proveedores.filter((p) => p.configurado).map((p) => p.nombre);
  contenedor.innerHTML = UI.badge(`activo · ${activos.join(' + ')}`, {
    variant: 'success', iconName: 'cheque',
  });
}

async function enviarPregunta() {
  const campo = $('#asistente-pregunta');
  const boton = $('#asistente-enviar');
  const texto = (campo.value || '').trim();
  if (!texto || asistenteEnviando) return;

  asistenteEnviando = true;
  boton.classList.add('btn-pending');
  boton.setAttribute('aria-busy', 'true');
  boton.disabled = true;

  agregarBurbuja('user', markdownBasico(texto));
  campo.value = '';
  const esperando = agregarPensando();

  try {
    const r = await api('/api/asistente', {
      method: 'POST',
      // Se manda el historial sin el mensaje actual: el servidor lo agrega.
      body: { pregunta: texto, historial: estado.conversacion },
    });

    estado.conversacion.push({ role: 'user', content: texto });
    estado.conversacion.push({ role: 'assistant', content: r.respuesta });

    esperando.remove();
    // Se crea la burbuja vacía y el texto se escribe de a poco adentro.
    const respuesta = agregarBurbuja('assistant', '', r);
    animarEscritura(respuesta.cuerpo, htmlRespuesta(r.respuesta), {
      alEscribir: () => bajarAlFinal(respuesta.contenedor),
    });
    adjuntarAsientosCSV(respuesta, r.asientos_csv);
  } catch (error) {
    esperando.remove();
    agregarFallo(error);
    // El mensaje del usuario no queda en el historial si nadie respondió:
    // de lo contrario la conversación se desincroniza con el servidor.
  } finally {
    asistenteEnviando = false;
    boton.classList.remove('btn-pending');
    boton.removeAttribute('aria-busy');
    boton.disabled = false;
    campo.focus();
  }
}

// ---------------------------------------------------------------------------
// Pira Pires flotante
// ---------------------------------------------------------------------------
//
// Es el mismo asistente que la pestaña, con dos diferencias: el avatar es la
// foto del pez (boca cerrada cuando espera, boca abierta mientras escribe) y
// se abre sobre la vista actual, sin cambiar de pestaña. La conversación es
// **compartida**: `estado.conversacion` es la misma lista para los dos, así
// que lo que se pregunta en la burbuja queda también en la pestaña.

/** ¿Está abierto el panel del asistente flotante? */
let piraAbierto = false;
/** ¿El usuario quitó la burbuja con el botón de basura? Entonces no vuelve. */
let piraEliminada = false;
/** Evita mandar dos preguntas a la vez desde la burbuja. */
let piraEnviando = false;
/**
 * Momento del último toque sobre la burbuja.
 *
 * Sirve para reconocer el doble toque, que devuelve la burbuja a su esquina.
 * Se reinicia cuando el panel se cierra de otra forma (la X, Escape, cambiar
 * de pestaña): así un toque normal nunca se confunde con un doble toque y el
 * chat siempre se abre.
 */
let ultimoToque = 0;

/** Abre o cierra el panel flotante y ajusta el foco y el ícono. */
function alternarPira(abrir) {
  const panel = $('#pira-panel');
  const burbuja = $('#pira-burbuja');
  if (!panel || !burbuja) return;

  piraAbierto = typeof abrir === 'boolean' ? abrir : !piraAbierto;

  panel.classList.toggle('oculto', !piraAbierto);
  burbuja.setAttribute('aria-expanded', String(piraAbierto));
  // El panel se abre pegado a la burbuja, así el chat aparece donde está el
  // pez y no en una esquina lejana.
  if (piraAbierto) {
    ubicarPanelJuntoAburbuja();
    // Abrir el chat equivale a leer lo que llegó: el aviso se apaga.
    mostrarNotificacionPira(false);
  }
  // Al cerrar, el pez vuelve a la boca cerrada. Al abrir **no** se toca la
  // foto: la elige quien llama (`enviarPreguntaPira` cuando piensa, y el
  // propio botón cuando saluda), así el gesto de saludo no se pisa.
  if (!piraAbierto) marcarPiraHablando(false);

  if (piraAbierto) {
    $('#pira-pregunta')?.focus();
  } else {
    burbuja.focus();
  }
}

/**
 * Cierra el panel flotante olvidando el doble toque pendiente.
 *
 * Se usa cuando el panel se cierra de una forma que no es un toque sobre la
 * burbuja (la X, Escape o al cambiar de pestaña). Así el próximo toque sobre
 * la burbuja se toma como un toque normal y no como la mitad de un doble toque
 * que ya no existe.
 */
function cerrarPira() {
  ultimoToque = 0;
  alternarPira(false);
}

/**
 * Coloca el panel flotante junto a la burbuja, sin salirse de la pantalla.
 *
 * Si la burbuja está en su esquina (sin `left` inline), el panel usa su
 * posición de hoja de estilos y no se toca nada. Si el usuario la arrastró,
 * el panel se ancla arriba o al costado según dónde haya quedado.
 */
function ubicarPanelJuntoAburbuja() {
  // La posición de arrastre vive en el **envoltorio**, no en el botón: es el
  // envoltorio el que se mueve y el que lleva `left`/`top`. Si se leyera el
  // botón, su `style.left` siempre estaría vacío y el panel se abriría en la
  // esquina aunque la burbuja estuviera en otro lado.
  const burbuja = $('#pira-envoltorio') || $('#pira-burbuja');
  const panel = $('#pira-panel');
  if (!burbuja || !panel) return;

  // Sin arrastre no hay nada que recalcular: manda el CSS.
  if (!burbuja.style.left) {
    panel.style.left = '';
    panel.style.top = '';
    panel.style.right = '';
    panel.style.bottom = '';
    return;
  }

  const caja = burbuja.getBoundingClientRect();
  const ancho = panel.offsetWidth || 480;
  const alto = panel.offsetHeight || 560;
  const margen = 12;
  const separacion = 10;
  let izquierda = caja.left + caja.width / 2 - ancho / 2;
  let arriba = caja.top - alto - separacion;

  // Si no cabe arriba, se abre debajo de la burbuja.
  if (arriba < margen) arriba = caja.bottom + separacion;
  // Si tampoco cabe debajo, se ancla al borde inferior.
  if (arriba + alto > window.innerHeight - margen) {
    arriba = Math.max(margen, window.innerHeight - alto - margen);
  }
  izquierda = Math.min(Math.max(margen, izquierda),
    Math.max(margen, window.innerWidth - ancho - margen));

  panel.style.left = `${izquierda}px`;
  panel.style.top = `${arriba}px`;
  panel.style.right = 'auto';
  panel.style.bottom = 'auto';
}

/**
 * Cambia la foto de Pira Pires.
 *
 * `hablando = true`  -> boca abierta (está redactando o dictando).
 * `hablando = false` -> boca cerrada (escucha o espera).
 *
 * Al tocar la burbuja flotante el pez abre la boca un instante, que es la
 * señal visual de que el asistente "tomó la palabra".
 */
function marcarPiraHablando(hablando) {
  const archivo = hablando
    ? 'img/pescadobocaabierta-avatar.png'
    : 'img/pescadobocacerrada-avatar.png';
  const burbuja = $('#pira-burbuja-imagen');
  const cabecera = $('#pira-cabecera-imagen');
  if (burbuja && !burbuja.src.endsWith(archivo)) burbuja.src = archivo;
  if (cabecera && !cabecera.src.endsWith(archivo)) cabecera.src = archivo;
}

/** Clave de `localStorage` donde se recuerda dónde quedó la burbuja. */
const CLAVE_POSICION = 'pira-posicion';
/** Cuántos píxeles hay que mover para que deje de ser un toque y pase a ser
 *  un arrastre. Por debajo de este umbral, el gesto abre el chat. */
const UMBRAL_ARRASTRE = 6;
/** Cuánto hay que mantener presionado, en móvil, para reiniciar la posición. */
const ESPERA_MANTENER = 650;
/** Ventana para reconocer un doble clic sobre la burbuja (en milisegundos). */
const ESPERA_DOBLE_CLIC = 350;

/** ¿Ya se conectaron los manejadores de la burbuja flotante? */
let piraConectada = false;

async function cargarPiraFlotante() {
  const burbuja = $('#pira-burbuja');
  const panel = $('#pira-panel');
  // El envoltorio es lo que se arrastra y lo que guarda la posición: así el
  // puntito de aviso y el botón de basura viajan con la burbuja.
  const envoltorio = $('#pira-envoltorio') || burbuja;
  if (!burbuja || !panel) return;

  // La posición guardada se restaura antes de cualquier cosa, así la burbuja
  // aparece donde el usuario la dejó y no salta de la esquina al cargar.
  restaurarPosicionPira(envoltorio);

  // Los manejadores se conectan **una sola vez**. Si se conectaran dos veces
  // (al salir y volver a entrar, por ejemplo), un solo toque abriría el chat y
  // lo cerraría en el mismo gesto: el chat no aparecería y la burbuja parecería
  // rota.
  if (!piraConectada) {
    piraConectada = true;

    conectarArrastrePira(envoltorio);

    // Escape cierra el panel, igual que un diálogo.
    document.addEventListener('keydown', (evento) => {
      if (evento.key === 'Escape' && piraAbierto) cerrarPira();
    });

    // Si cambia el tamaño de la ventana, la burbuja y el panel se reacomodan
    // para no quedar fuera de la pantalla.
    window.addEventListener('resize', () => {
      if (envoltorio.style.left) {
        const caja = envoltorio.getBoundingClientRect();
        colocarBurbuja(envoltorio, caja.left, caja.top);
      }
      if (piraAbierto) ubicarPanelJuntoAburbuja();
    });
  }

  $('#pira-cerrar').onclick = () => cerrarPira();

  $('#pira-formulario').onsubmit = (evento) => {
    evento.preventDefault();
    enviarPreguntaPira();
  };

  // Adjuntar archivos: el botón de clip abre el selector escondido.
  const campoArchivo = $('#pira-archivo');
  const botonAdjuntar = $('#pira-adjuntar');
  if (botonAdjuntar && campoArchivo) {
    botonAdjuntar.onclick = () => campoArchivo.click();
    campoArchivo.onchange = async () => {
      await adjuntarArchivosPira(campoArchivo.files);
      // Se limpia el input para poder volver a elegir el mismo archivo.
      campoArchivo.value = '';
    };
  }
  // Quitar un adjunto: delegado, porque las etiquetas se repintan enteras.
  const listaArchivos = $('#pira-archivos');
  if (listaArchivos) {
    UI.delegar(listaArchivos, '[data-quitar-archivo]', (elemento) => {
      quitarArchivoPira(Number(elemento.dataset.quitarArchivo));
    });
  }

  // El botón de basura quita la burbuja y su panel (preguntando antes).
  const botonEliminar = $('#pira-eliminar');
  if (botonEliminar) botonEliminar.onclick = eliminarPira;
}

/**
 * Muestra u oculta el puntito de aviso de la burbuja.
 *
 * Es el aviso de «hay una respuesta nueva»: se prende cuando Pira Pires termina
 * de contestar con el panel cerrado, y se apaga al abrirlo. Nunca se prende con
 * el panel abierto, porque ahí la respuesta se está viendo.
 */
function mostrarNotificacionPira(mostrar) {
  const punto = $('#pira-notificacion');
  if (punto) punto.classList.toggle('visible', Boolean(mostrar));
}

/**
 * Quita la burbuja de la pantalla, pero preguntando antes.
 *
 * El botón de basura está apoyado sobre la esquina de la burbuja, así que un
 * toque casi proprio de abrir el chat puede terminar borrándola. Por eso se
 * pide confirmación y, además, el aviso que sale trae un «Deshacer».
 */
function eliminarPira() {
  UI.confirmar({
    titulo: '¿Quitar la burbuja de Pira Pires?',
    descripcion: 'El chat flotante desaparece de la pantalla. Si te arrepentís, '
      + 'lo podés volver a mostrar desde el aviso que aparece abajo.',
    confirmarTexto: 'Quitar burbuja',
    icono: 'papelera',
  }).then((aceptado) => {
    if (aceptado) quitarBurbujaPira();
  });
}

/** Quita la burbuja de verdad: la esconde, cierra el panel y olvida todo. */
function quitarBurbujaPira() {
  cerrarPira();
  mostrarNotificacionPira(false);
  limpiarArchivosPira();

  const envoltorio = $('#pira-envoltorio');
  const burbuja = $('#pira-burbuja');
  if (envoltorio) envoltorio.classList.add('oculto');
  if (burbuja) burbuja.classList.add('oculto');
  // Queda marcada como eliminada: así no reaparece al cambiar de pestaña.
  piraEliminada = true;

  try {
    localStorage.removeItem(CLAVE_POSICION);
  } catch {
    // Sin almacenamiento: no hay nada que olvidar.
  }

  UI.toast({
    variant: 'info',
    title: 'Burbuja quitada',
    description: 'Pira Pires dejó de aparecer flotando en la pantalla.',
    accion: { label: 'Deshacer', onPress: restaurarBurbujaPira },
  });
}

/** Vuelve a mostrar la burbuja que se había quitado. */
function restaurarBurbujaPira() {
  piraEliminada = false;
  const envoltorio = $('#pira-envoltorio');
  const burbuja = $('#pira-burbuja');
  if (envoltorio) envoltorio.classList.remove('oculto');
  if (burbuja) burbuja.classList.remove('oculto');
  // Si la vista activa es la del asistente, la burbuja sigue escondida: ahí
  // está el mismo chat a pantalla completa.
  actualizarBurbujaSegunVista(estado.vista);
  avisar('La burbuja de Pira Pires volvió a la pantalla.', 'success');
}

/**
 * Muestra u oculta la burbuja flotante según la vista activa.
 *
 * En la pestaña **Asistente** la burbuja sobra —ahí está el mismo chat, a
 * pantalla completa— y encima tapa el contenido. Se esconde mientras dura esa
 * pestaña y vuelve sola al salir. Si el usuario la eliminó a mano, no vuelve.
 */
function actualizarBurbujaSegunVista(vista) {
  if (piraEliminada) return;
  const envoltorio = $('#pira-envoltorio');
  if (!envoltorio) return;
  const ocultar = vista === 'asistente';
  envoltorio.classList.toggle('oculto', ocultar);
  if (ocultar) {
    // Al esconderla también se cierra el panel flotante: no tendría de dónde
    // colgar y quedaría flotando solo.
    cerrarPira();
  }
}

/**
 * Hace que la burbuja se pueda arrastrar como la de Messenger.
 *
 * Se usan **eventos de puntero**, que unifican mouse, dedo y lápiz. El gesto
 * se resuelve así:
 *
 *   · mover menos de `UMBRAL_ARRASTRE` px  -> es un toque: abre o cierra el chat
 *   · mover más                            -> es un arrastre: la burbuja sigue
 *                                             al puntero y queda donde se suelte
 *   · doble clic (o doble toque)           -> vuelve a su esquina original
 *   · mantener presionado en móvil         -> vuelve a su esquina original
 *
 * La posición queda dentro de la pantalla y se recuerda entre visitas.
 */
function conectarArrastrePira(burbuja) {
  let arrastrando = false;
  let movido = false;
  let inicio = { x: 0, y: 0, izq: 0, arriba: 0 };
  let puntero = null;
  let esperaMantener = null;

  const cancelarMantener = () => {
    if (esperaMantener !== null) {
      clearTimeout(esperaMantener);
      esperaMantener = null;
    }
  };

  burbuja.addEventListener('pointerdown', (evento) => {
    // Solo el botón principal del mouse; el resto (rueda, botón derecho) no.
    if (evento.button !== 0) return;
    puntero = evento.pointerId;
    arrastrando = true;
    movido = false;

    const caja = burbuja.getBoundingClientRect();
    inicio = { x: evento.clientX, y: evento.clientY, izq: caja.left, arriba: caja.top };

    // En móvil (sin mouse) mantener presionado reinicia la posición. Se
    // arma un temporizador que se cancela apenas el dedo se mueve.
    if (evento.pointerType !== 'mouse') {
      cancelarMantener();
      esperaMantener = setTimeout(() => {
        if (!movido) {
          reiniciarPosicionPira(burbuja);
          arrastrando = false;
        }
      }, ESPERA_MANTENER);
    }
  });

  burbuja.addEventListener('pointermove', (evento) => {
    if (!arrastrando || evento.pointerId !== puntero) return;

    const dx = evento.clientX - inicio.x;
    const dy = evento.clientY - inicio.y;

    if (!movido && Math.hypot(dx, dy) < UMBRAL_ARRASTRE) return;

    // Pasado el umbral, es un arrastre: se cancela el gesto de mantener y se
    // pasa a posicionamiento absoluto con `left`/`top` en píxeles.
    if (!movido) {
      movido = true;
      cancelarMantener();
      burbuja.classList.add('arrastrando');
      burbuja.setPointerCapture(evento.pointerId);
      pasarAAbsoluta(burbuja, inicio.izq, inicio.arriba);
    }

    colocarBurbuja(burbuja, inicio.izq + dx, inicio.arriba + dy);
  });

  const soltar = (evento) => {
    if (!arrastrando || evento.pointerId !== puntero) return;
    cancelarMantener();
    arrastrando = false;
    puntero = null;
    burbuja.classList.remove('arrastrando');

    if (movido) {
      // Fue un arrastre: se guarda dónde quedó y **no** se abre el chat.
      guardarPosicionPira(burbuja);
      return;
    }

    // Fue un toque. Si el dedo cayó en el botón de basura, ese toque es para
    // la basura y no para el chat: no se toca el panel.
    if (esBotonEliminar(evento.target)) return;

    // Si viene muy pegado al anterior, es un doble clic: se
    // cancela el primer toque (que ya había abierto o cerrado el panel) y en
    // su lugar se devuelve la burbuja a su esquina, sin tocar el panel. Así el
    // doble clic no deja el chat abierto y cerrado de un tirón.
    const ahora = Date.now();
    if (ahora - ultimoToque < ESPERA_DOBLE_CLIC) {
      ultimoToque = 0;
      // Se deshace el toque anterior y se devuelve la burbuja a su esquina.
      alternarPira(!piraAbierto);
      reiniciarPosicionPira(burbuja);
      return;
    }
    ultimoToque = ahora;

    // Un solo toque abre o cierra el panel, con el saludo del pez al abrir.
    const abrir = !piraAbierto;
    if (abrir) {
      marcarPiraHablando(true);
      setTimeout(() => marcarPiraHablando(false), 450);
    }
    alternarPira(abrir);
  };

  burbuja.addEventListener('pointerup', soltar);
  burbuja.addEventListener('pointercancel', (evento) => {
    cancelarMantener();
    if (arrastrando && movido) guardarPosicionPira(burbuja);
    arrastrando = false;
    puntero = null;
    burbuja.classList.remove('arrastrando');
    void evento;
  });
}

/** ¿El elemento es el botón de basura de la burbuja? */
function esBotonEliminar(elemento) {
  return Boolean(elemento && elemento.closest && elemento.closest('#pira-eliminar'));
}

/** Pasa la burbuja de `right`/`bottom` a `left`/`top` para poder arrastrarla. */
function pasarAAbsoluta(burbuja, izquierda, arriba) {
  burbuja.style.right = 'auto';
  burbuja.style.bottom = 'auto';
  burbuja.style.left = `${izquierda}px`;
  burbuja.style.top = `${arriba}px`;
}

/**
 * Coloca la burbuja en un punto, sin dejarla salirse de la pantalla.
 * El margen es el mismo que el de su esquina, así no queda pegada al borde.
 */
function colocarBurbuja(burbuja, izquierda, arriba) {
  const lado = burbuja.offsetWidth || 60;
  const margen = 8;
  const maxIzq = Math.max(margen, window.innerWidth - lado - margen);
  const maxArriba = Math.max(margen, window.innerHeight - lado - margen);
  burbuja.style.left = `${Math.min(Math.max(margen, izquierda), maxIzq)}px`;
  burbuja.style.top = `${Math.min(Math.max(margen, arriba), maxArriba)}px`;
}

/** Guarda la posición actual para la próxima visita, si el navegador deja. */
function guardarPosicionPira(burbuja) {
  try {
    const caja = burbuja.getBoundingClientRect();
    localStorage.setItem(CLAVE_POSICION, JSON.stringify({
      izquierda: Math.round(caja.left),
      arriba: Math.round(caja.top),
      ancho: window.innerWidth,
      alto: window.innerHeight,
    }));
  } catch {
    // Sin almacenamiento (modo privado): la posición simplemente no se recuerda.
  }
}

/** Restaura la posición guardada, escalándola si cambió el tamaño de la ventana. */
function restaurarPosicionPira(burbuja) {
  let guardada;
  try {
    guardada = JSON.parse(localStorage.getItem(CLAVE_POSICION) || 'null');
  } catch {
    guardada = null;
  }
  if (!guardada || typeof guardada.izquierda !== 'number') return;

  // Si la ventana cambió de tamaño desde la última vez, la posición se
  // reescala para que la burbuja siga cayendo en el mismo lugar relativo.
  const escalaX = guardada.ancho ? window.innerWidth / guardada.ancho : 1;
  const escalaY = guardada.alto ? window.innerHeight / guardada.alto : 1;
  pasarAAbsoluta(burbuja,
    guardada.izquierda * escalaX, guardada.arriba * escalaY);
  colocarBurbuja(burbuja, guardada.izquierda * escalaX, guardada.arriba * escalaY);
}

/** Devuelve la burbuja a su esquina original y olvida la posición guardada. */
function reiniciarPosicionPira(burbuja) {
  burbuja.style.left = '';
  burbuja.style.top = '';
  burbuja.style.right = '';
  burbuja.style.bottom = '';
  try {
    localStorage.removeItem(CLAVE_POSICION);
  } catch {
    // Nada que limpiar.
  }
  // Un gesto corto de confirmación: el pez abre la boca un instante.
  marcarPiraHablando(true);
  setTimeout(() => marcarPiraHablando(false), 450);
}

async function enviarPreguntaPira() {
  const campo = $('#pira-pregunta');
  const boton = $('#pira-enviar');
  const texto = (campo.value || '').trim();
  // Se puede enviar solo un archivo, sin escribir nada: es común adjuntar un
  // balance y dejar que Pira Pires lo mire y pregunte qué hacer con él.
  if ((!texto && !piraArchivos.length) || piraEnviando) return;

  piraEnviando = true;
  boton.classList.add('btn-pending');
  boton.disabled = true;

  const historial = $('#pira-historial');
  const opciones = { contenedor: historial, flotante: true };

  // El mensaje del usuario muestra la pregunta y, si hay, los adjuntos.
  const resumen = resumenAdjuntosPira();
  const htmlUsuario = markdownBasico(texto || '(archivo adjunto)')
    + (resumen ? `<div class="mt-1.5 text-xs opacity-80">${resumen}</div>` : '');
  agregarBurbuja('user', htmlUsuario, null, opciones);
  campo.value = '';

  // Los archivos se mandan una sola vez: a partir de acá ya viajan en el
  // historial como texto y volver a mandarlos duplicaría el contexto.
  const archivos = piraArchivos.slice();
  limpiarArchivosPira();

  // Mientras piensa, el pez abre la boca: es el indicador de actividad.
  marcarPiraHablando(true);
  const esperando = agregarPensando(opciones);

  try {
    const r = await api('/api/asistente', {
      method: 'POST',
      body: { pregunta: texto, historial: estado.conversacion, archivos },
    });

    estado.conversacion.push({ role: 'user', content: texto || '(archivo adjunto)' });
    estado.conversacion.push({ role: 'assistant', content: r.respuesta });

    esperando.remove();
    // La burbuja se crea vacía y Pira la escribe de a poco: mientras redacta
    // mantiene la boca abierta, y la cierra recién cuando termina.
    const respuesta = agregarBurbuja('assistant', '', r, opciones);
    animarEscritura(respuesta.cuerpo, htmlRespuesta(r.respuesta), {
      alEscribir: () => bajarAlFinal(respuesta.contenedor),
      alTerminar: () => {
        marcarPiraHablando(false);
        // Si el panel quedó cerrado mientras escribía, avisa con el puntito.
        if (!piraAbierto) mostrarNotificacionPira(true);
      },
    });
    // La pestaña también muestra la conversación: se replica ahí si está
    // montada, para que las dos vistas no se contradigan.
    adjuntarAsientosCSV(respuesta, r.asientos_csv);
    replicarEnPestana(texto || '(archivo adjunto)', r);
  } catch (error) {
    esperando.remove();
    agregarFallo(error, opciones);
    // Sin respuesta no hay animación que cierre la boca.
    marcarPiraHablando(false);
  } finally {
    piraEnviando = false;
    boton.classList.remove('btn-pending');
    boton.disabled = false;
    campo.focus();
  }
}

// ---------------------------------------------------------------------------
// Archivos adjuntos de Pira Pires
// ---------------------------------------------------------------------------
//
// Pira Pires puede recibir imágenes (capturas de pantalla, fotos de libros o
// balances), PDF, CSV, TXT y JSON. Todo se lee acá, en el navegador, y viaja
// en base64; el servidor decide qué hacer con cada tipo. Los archivos quedan
// en memoria mientras dura la pregunta: se adjuntan, se ven en una tira de
// etiquetas, y se envían una sola vez con la próxima pregunta.

/** Archivos adjuntos pendientes de enviar. */
let piraArchivos = [];

/** Tope de tamaño por archivo, para no colgar la conversación. */
const MAXIMO_ARCHIVO = 8 * 1024 * 1024;   // 8 MB

/** Lee un archivo como base64, sin el encabezado `data:...;base64,`. */
function leerArchivoBase64(archivo) {
  return new Promise((resolver, rechazar) => {
    const lector = new FileReader();
    lector.onload = () => {
      const datos = String(lector.result || '');
      const coma = datos.indexOf(',');
      resolver(coma >= 0 ? datos.slice(coma + 1) : datos);
    };
    lector.onerror = () => rechazar(new Error('No se pudo leer el archivo.'));
    lector.readAsDataURL(archivo);
  });
}

/** Toma los archivos elegidos en el input y los prepara para enviar. */
async function adjuntarArchivosPira(lista) {
  const elegidos = Array.from(lista || []);
  if (!elegidos.length) return;

  for (const archivo of elegidos) {
    if (archivo.size > MAXIMO_ARCHIVO) {
      avisar(`«${archivo.name}» es más grande de 8 MB y no se adjuntó.`, 'error');
      continue;
    }
    try {
      const contenido = await leerArchivoBase64(archivo);
      piraArchivos.push({
        nombre: archivo.name,
        tipo: archivo.type || 'application/octet-stream',
        tamano: archivo.size,
        contenido,
      });
    } catch {
      avisar(`No se pudo leer «${archivo.name}».`, 'error');
    }
  }
  pintarArchivosPira();
}

/** Quita un adjunto por su índice. */
function quitarArchivoPira(indice) {
  piraArchivos.splice(indice, 1);
  pintarArchivosPira();
}

/** Vacía la lista de adjuntos. */
function limpiarArchivosPira() {
  piraArchivos = [];
  pintarArchivosPira();
}

/** ¿El archivo es una imagen (se puede mostrar una miniatura)? */
function esImagenPira(archivo) {
  return /^image\//.test(archivo.tipo) || /\.(png|jpe?g|gif|webp|bmp)$/i.test(archivo.nombre);
}

/** Dibuja la tira de adjuntos sobre el pie del panel. */
function pintarArchivosPira() {
  const contenedor = $('#pira-archivos');
  if (!contenedor) return;
  if (!piraArchivos.length) {
    contenedor.classList.add('oculto');
    contenedor.innerHTML = '';
    return;
  }
  contenedor.classList.remove('oculto');
  contenedor.innerHTML = piraArchivos.map((archivo, indice) => {
    const miniatura = esImagenPira(archivo)
      ? `<img class="archivo-chip-miniatura" src="data:${archivo.tipo};base64,${archivo.contenido}" alt="">`
      : UI.icono('archivo', { tamano: 16, clase: 'shrink-0 text-surface-400' });
    return `<span class="archivo-chip">
      ${miniatura}
      <span class="archivo-chip-nombre" title="${esc(archivo.nombre)}">
        ${esc(archivo.nombre)}
      </span>
      <button type="button" class="archivo-chip-quitar" data-quitar-archivo="${indice}"
              aria-label="Quitar ${esc(archivo.nombre)}">&times;</button>
    </span>`;
  }).join('');
}

/** Resumen corto de los adjuntos, para el mensaje del usuario. */
function resumenAdjuntosPira() {
  if (!piraArchivos.length) return '';
  const nombres = piraArchivos.map((a) => esc(a.nombre)).join(', ');
  return `📎 ${piraArchivos.length === 1 ? 'Archivo' : 'Archivos'}: ${nombres}`;
}

// ---------------------------------------------------------------------------
// Asientos propuestos por Pira Pires (CSV del importador)
// ---------------------------------------------------------------------------
//
// Cuando el usuario le pasa un borrador de operaciones, el asistente responde
// con los asientos en un bloque ```csv```, en el mismo formato que la
// plantilla del importador. El servidor extrae ese bloque (`asientos_csv`):
// acá se saca del texto de la burbuja y se muestra aparte, como tabla, con
// botones para copiarlo o descargarlo.

/** Markdown de la respuesta sin los bloques de código (el CSV va aparte). */
function htmlRespuesta(texto) {
  const limpio = String(texto || '').replace(/```[^\n]*\n[\s\S]*?```/g, '').trim();
  return markdownBasico(limpio || texto || '');
}

/** Separa el CSV de asientos en filas de celdas (separador `;`). */
function filasCSVAsientos(csv) {
  return String(csv || '').trim().split(/\r?\n/).filter(Boolean)
    .map((linea) => linea.split(';').map((celda) => celda.trim()));
}

/** Tabla con las líneas de los asientos propuestos, agrupadas por asiento. */
function tablaAsientosPropuestos(csv) {
  const [, ...filas] = filasCSVAsientos(csv);
  let anterior = null;
  const cuerpo = filas.map((f) => {
    const cabecera = f[0] !== anterior
      ? `<tr class="bg-surface-100/60"><td colspan="4" class="font-semibold">
           Asiento ${esc(f[0] || '')} · ${esc(f[1] || '')} · ${esc(f[2] || '')}
         </td></tr>`
      : '';
    anterior = f[0];
    return `${cabecera}
      <tr>
        <td class="font-mono whitespace-nowrap">${esc(f[3] || '')}</td>
        <td>${esc(f[4] || '')}</td>
        <td class="whitespace-nowrap text-right tabular-nums">${esc(f[5] || '')}</td>
        <td class="whitespace-nowrap text-right tabular-nums">${esc(f[6] || '')}</td>
      </tr>`;
  }).join('');
  return `
    <div class="max-h-56 overflow-auto rounded-field border border-surface-200">
      <table class="table text-xs">
        <thead><tr>
          <th>Cuenta</th><th>Detalle</th>
          <th class="text-right">Debe</th><th class="text-right">Haber</th>
        </tr></thead>
        <tbody>${cuerpo}</tbody>
      </table>
    </div>`;
}

/** Descarga un texto como archivo, sin pasar por el servidor. */
function descargarTexto(texto, nombre, tipo = 'text/csv;charset=utf-8') {
  // El BOM hace que Excel abra el CSV con los acentos bien.
  descargarBlob(new Blob(['\ufeff', texto], { type: tipo }), nombre);
}

/** Guarda un Blob con el nombre indicado. */
function descargarBlob(blob, nombre) {
  const url = URL.createObjectURL(blob);
  const enlace = document.createElement('a');
  enlace.href = url;
  enlace.download = nombre;
  document.body.appendChild(enlace);
  enlace.click();
  enlace.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Tarjeta con los asientos propuestos, debajo de la burbuja de respuesta. */
function adjuntarAsientosCSV(respuesta, csv) {
  if (!csv || !respuesta?.caja) return;
  const destino = respuesta.caja.querySelector('.min-w-0') || respuesta.caja;
  const asientos = new Set(filasCSVAsientos(csv).slice(1).map((f) => f[0])).size;

  const tarjeta = document.createElement('div');
  tarjeta.className = 'asientos-propuestos mt-2 space-y-2 rounded-field border '
    + 'border-primary-200 bg-primary-50/40 p-3';
  tarjeta.innerHTML = `
    <p class="text-xs font-semibold text-surface-700">
      ${UI.icono('archivo', { tamano: 14 })}
      Asientos propuestos: ${asientos} · formato CSV del importador
    </p>
    ${tablaAsientosPropuestos(csv)}
    <div class="flex flex-wrap gap-2">
      <button type="button" class="btn btn-sm btn-primary-fill" data-accion="importar">
        ${UI.icono('tilde', { tamano: 14 })} Revisar e importar al Libro Diario
      </button>
      <button type="button" class="btn btn-sm btn-secondary-outline" data-accion="copiar">
        Copiar CSV
      </button>
      <button type="button" class="btn btn-sm btn-secondary-outline" data-accion="csv">
        ${UI.icono('descargar', { tamano: 14 })} CSV
      </button>
      <button type="button" class="btn btn-sm btn-secondary-outline" data-accion="xlsx">
        ${UI.icono('descargar', { tamano: 14 })} Libro Diario en Excel
      </button>
    </div>`;

  const botonImportar = tarjeta.querySelector('[data-accion="importar"]');
  botonImportar.onclick = async () => {
    botonImportar.disabled = true;
    const resultado = await importarAsientosTexto({ csv }, {
      titulo: 'Revisar asientos de Pira Pires',
      tituloErrores: 'Los asientos propuestos tienen errores',
      ayudaErrores: 'Podés pedirle a Pira Pires que los corrija: copiá el error '
        + 'en el chat y volvé a intentarlo con la nueva propuesta.',
    });
    if (resultado) {
      // Ya están en el Libro Diario: no se ofrece importarlos dos veces.
      botonImportar.innerHTML = `${UI.icono('cheque', { tamano: 14 })} `
        + `Importados (${resultado.creados})`;
    } else {
      botonImportar.disabled = false;
    }
  };

  tarjeta.querySelector('[data-accion="copiar"]').onclick = async () => {
    try {
      await navigator.clipboard.writeText(csv);
      avisar('El CSV quedó en el portapapeles.', 'success', 'Copiado');
    } catch {
      avisar('No se pudo copiar: descargalo como archivo.', 'warning');
    }
  };
  const nombre = `asientos-pira-pires-${new Date().toISOString().slice(0, 10)}`;
  tarjeta.querySelector('[data-accion="csv"]').onclick = () =>
    descargarTexto(csv, `${nombre}.csv`);
  tarjeta.querySelector('[data-accion="xlsx"]').onclick = async () => {
    try {
      const respuesta = await fetch('/api/excel/desde-csv', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ csv, nombre, formato: 'libro' }),
      });
      if (!respuesta.ok) throw new Error(`Error ${respuesta.status}`);
      descargarBlob(await respuesta.blob(), `${nombre}.xlsx`);
    } catch (error) {
      avisar(`No se pudo generar el Excel: ${error.message}`, 'danger');
    }
  };

  destino.appendChild(tarjeta);
  bajarAlFinal(respuesta.contenedor);
}

/** Refleja en la pestaña del asistente una charla hecha desde la burbuja. */
function replicarEnPestana(texto, respuesta) {
  const contenedor = $('#asistente-historial');
  if (!contenedor) return;
  agregarBurbuja('user', markdownBasico(texto));
  const burbuja = agregarBurbuja('assistant', '', respuesta);
  animarEscritura(burbuja.cuerpo, htmlRespuesta(respuesta.respuesta), {
    alEscribir: () => bajarAlFinal(contenedor),
  });
  adjuntarAsientosCSV(burbuja, respuesta.asientos_csv);
}

/** Agrega una burbuja al historial y baja hasta el final.
 *
 * Devuelve un objeto con las dos partes que hacen falta para animar la
 * respuesta: el nodo exterior (`caja`) y el interior, donde vive el HTML
 * (`cuerpo`). Para el usuario se devuelve el mismo nodo en ambos campos. */
/**
 * Avatar del asistente para las burbujas del chat.
 *
 * Es siempre la foto de Pira Pires: la misma cara en la pestaña y en el panel
 * flotante. En la pestaña usa el retrato cuadrado; en el flotante, el pez con
 * la boca cerrada, que es el que abre y cierra la boca al hablar.
 */
function avatarAsistente(flotante) {
  const src = flotante
    ? 'img/pescadobocacerrada-avatar.png'
    : 'img/pirapires-cuadrado.jpg';
  const medida = flotante ? 'size-7' : 'size-8';
  return `<img src="${src}" alt="" aria-hidden="true"
            class="mt-0.5 ${medida} shrink-0 rounded-full object-cover">`;
}

function agregarBurbuja(rol, html, meta = null, opciones = {}) {
  const contenedor = opciones.contenedor || $('#asistente-historial');
  const flotante = opciones.flotante === true;
  const esUsuario = rol === 'user';

  const insignia = meta
    ? UI.badge(`${meta.proveedor} · ${(meta.tiempo_ms / 1000).toFixed(1)} s`, {
        variant: meta.proveedor === 'groq' ? 'primary' : 'secondary',
      })
    : '';

  const burbuja = document.createElement('div');
  burbuja.className = 'flex items-start gap-3' + (esUsuario ? ' justify-end' : '');

  if (esUsuario) {
    burbuja.innerHTML =
      `<div class="burbuja burbuja-usuario">${html}</div>`;
  } else {
    // El avatar es la foto de Pira Pires en las dos vistas: la misma cara en
    // la pestaña y en el panel flotante. (Antes la pestaña mostraba una «P»
    // sobre un círculo, que no coincidía con el pez de la burbuja.)
    burbuja.innerHTML = `${avatarAsistente(flotante)}
       <div class="min-w-0 max-w-[85%]">
         <div class="burbuja burbuja-asistente">${html}</div>
         ${insignia ? `<div class="mt-1.5">${insignia}</div>` : ''}
       </div>`;
  }

  contenedor.appendChild(burbuja);
  bajarAlFinal(contenedor);
  const cuerpo = esUsuario ? burbuja : burbuja.querySelector('.burbuja-asistente');
  return { caja: burbuja, cuerpo, contenedor };
}

// ---------------------------------------------------------------------------
// Escritura progresiva de las respuestas
// ---------------------------------------------------------------------------
//
// El modelo no transmite el texto: cuando contesta, ya está completo. Para que
// la respuesta no caiga de golpe, se revela acá palabra por palabra. El ritmo
// se adapta al largo del texto, así una respuesta corta no se hace esperar y
// una larga no tarda una eternidad. Mientras escribe, el pez tiene la boca
// abierta; al terminar, el cursor deja de parpadear.

/** Duración total de la animación según el largo del texto. */
function duracionEscritura(largo) {
  if (largo <= 200) return 400;      // respuesta corta: casi instantánea
  if (largo <= 600) return 900;      // mediana
  if (largo <= 1400) return 1500;    // larga
  return 2200;                       // muy larga: se corta acá, no se estira más
}

/**
 * Escribe el HTML ya renderizado dentro de la burbuja, palabra por palabra.
 *
 * Se anima sobre el HTML **final**: se envuelve cada palabra visible en un
 * `<span>` que arranca invisible y se va prendiendo de a uno. Así el Markdown
 * (negritas, listas, tablas) se ve formateado desde el primer instante y las
 * etiquetas nunca quedan abiertas a la mitad.
 *
 * @param {HTMLElement} cuerpo  nodo donde se escribe
 * @param {string} html         HTML final, ya escapado y renderizado
 * @param {object} opciones     { alTerminar, alEscribir }
 */
function animarEscritura(cuerpo, html, opciones = {}) {
  const alTerminar = opciones.alTerminar || (() => {});
  const alEscribir = opciones.alEscribir || (() => {});

  cuerpo.classList.add('cursor-escritura');
  cuerpo.innerHTML = marcarPalabras(html);

  const palabras = cuerpo.querySelectorAll('.palabra-oculta');
  const total = palabras.length;

  if (!total) {
    cuerpo.classList.remove('cursor-escritura');
    alTerminar();
    return;
  }

  const duracion = duracionEscritura(html.length);
  // Se revela de a un lote: si el texto tiene 300 palabras y hay 900 ms,
  // conviene prender varias por tick en lugar de una cada 3 ms.
  const porTick = Math.max(1, Math.ceil(total / (duracion / 40)));
  let reveladas = 0;

  const paso = () => {
    for (let i = 0; i < porTick && reveladas < total; i += 1, reveladas += 1) {
      palabras[reveladas].classList.remove('palabra-oculta');
    }
    alEscribir();
    if (reveladas < total) {
      setTimeout(paso, 40);
    } else {
      cuerpo.classList.remove('cursor-escritura');
      alTerminar();
    }
  };
  paso();
}

/**
 * Envuelve cada palabra visible del HTML en un `<span>` invisible.
 *
 * Se camina el HTML carácter por carácter llevando la cuenta de si se está
 * dentro de una etiqueta (`<...>`). El texto que está fuera de etiquetas se
 * envuelve palabra por palabra; las etiquetas se copian tal cual. Es lo que
 * permite animar sin romper el marcado.
 */
function marcarPalabras(html) {
  const salida = [];
  let texto = '';           // texto acumulado fuera de etiquetas
  let dentroDeEtiqueta = false;

  const volcarTexto = () => {
    if (!texto) return;
    // Se separan las palabras de los espacios para conservar el espaciado.
    for (const parte of texto.split(/(\s+)/)) {
      if (!parte) continue;
      salida.push(/^\s+$/.test(parte)
        ? parte
        : `<span class="palabra-oculta">${parte}</span>`);
    }
    texto = '';
  };

  for (const caracter of html) {
    if (caracter === '<') {
      volcarTexto();
      dentroDeEtiqueta = true;
    }
    if (dentroDeEtiqueta) {
      salida.push(caracter);
      if (caracter === '>') dentroDeEtiqueta = false;
    } else {
      texto += caracter;
    }
  }
  volcarTexto();
  return salida.join('');
}

/** Indicador mientras el proveedor responde. Se quita al llegar la respuesta. */
function agregarPensando(opciones = {}) {
  const contenedor = opciones.contenedor || $('#asistente-historial');
  const flotante = opciones.flotante === true;
  const caja = document.createElement('div');
  caja.className = 'flex items-start gap-3';
  caja.innerHTML = `
    ${avatarAsistente(flotante)}
    <div class="flex items-center gap-1.5 rounded-lg rounded-tl-none border
                border-surface-200 bg-surface-50 px-3.5 py-3">
      <span class="skeleton size-2"></span>
      <span class="skeleton size-2"></span>
      <span class="skeleton size-2"></span>
      <span class="ml-1 text-xs text-surface-500">Consultando a Groq…</span>
    </div>`;
  contenedor.appendChild(caja);
  bajarAlFinal(contenedor);
  return caja;
}

/** Falla del asistente: aviso en pantalla y en el historial. */
function agregarFallo(error, opciones = {}) {
  avisarError(error);
  const contenedor = opciones.contenedor || $('#asistente-historial');
  const caja = document.createElement('div');
  caja.className = 'flex items-start gap-3';
  const mensaje = error?.mensaje || 'No se pudo obtener respuesta.';
  caja.innerHTML = `
    <div class="grid size-8 shrink-0 place-items-center rounded-full
                bg-warning-50 text-sm font-bold text-warning-600">!</div>
    <div class="burbuja burbuja-error">
      <strong>No pude responder.</strong> ${esc(mensaje)}
    </div>`;
  contenedor.appendChild(caja);
  bajarAlFinal(contenedor);
}

function bajarAlFinal(contenedor) {
  contenedor.scrollTop = contenedor.scrollHeight;
}

/**
 * Renderizador mínimo y seguro de Markdown para las respuestas del modelo.
 *
 * El texto se escapa **primero** y sólo después se reconocen los marcados;
 * así ningún contenido del modelo puede inyectar HTML en la página.
 * Soporta: negritas, cursivas, código, títulos, listas, tablas y separadores.
 */
function markdownBasico(texto) {
  const lineas = esc(texto).replace(/\r/g, '').split('\n');
  const salida = [];
  let lista = null;    // 'ul' | 'ol' | null
  let tabla = false;
  let esCabecera = false;

  const cerrarLista = () => {
    if (lista) { salida.push(lista === 'ul' ? '</ul>' : '</ol>'); lista = null; }
  };
  const cerrarTabla = () => {
    if (tabla) { salida.push('</tbody></table></div>'); tabla = false; }
  };
  const cerrarTodo = () => { cerrarLista(); cerrarTabla(); };

  const enLinea = (t) => t
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/`([^`]+)`/g, '<code class="codigo">$1</code>');

  for (const cruda of lineas) {
    const l = cruda.trim();

    if (!l) { cerrarTodo(); continue; }
    if (/^(-{3,}|={3,})$/.test(l)) {
      cerrarTodo();
      salida.push('<hr class="my-3 border-surface-200">');
      continue;
    }

    // --- Tabla: fila que empieza y termina con | -----------------------
    if (l.startsWith('|')) {
      if (/^\|[\s:|-]+\|$/.test(l)) continue;      // fila separadora de columnas
      const celdas = l.replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|').map((c) => c.trim());
      if (!tabla) {
        // La primera fila es el encabezado: va en <thead> para que los
        // lectores de pantalla la anuncien como cabecera de columna.
        cerrarLista();
        salida.push('<div class="scroll-area overflow-x-auto">'
          + '<table class="table"><thead>');
        tabla = true;
        esCabecera = true;
      }
      if (esCabecera) {
        salida.push(`<tr>${celdas
          .map((c) => `<th class="text-xs font-semibold text-surface-500">`
            + `${enLinea(c)}</th>`)
          .join('')}</tr></thead><tbody>`);
        esCabecera = false;
      } else {
        salida.push(`<tr>${celdas
          .map((c) => `<td>${enLinea(c)}</td>`)
          .join('')}</tr>`);
      }
      continue;
    }
    cerrarTabla();

    // --- Título --------------------------------------------------------
    const encabezado = l.match(/^(#{1,6})\s+(.*)$/);
    if (encabezado) {
      cerrarTodo();
      const tamano = encabezado[1].length <= 2 ? 'text-sm' : 'text-xs';
      salida.push(
        `<p class="${tamano} font-semibold text-surface-900 mt-1">`
        + `${enLinea(encabezado[2])}</p>`
      );
      continue;
    }

    // --- Listas --------------------------------------------------------
    const vineta = l.match(/^[-*]\s+(.*)$/);
    const numerada = l.match(/^\d+[.)]\s+(.*)$/);
    if (vineta || numerada) {
      cerrarTabla();
      const deseada = vineta ? 'ul' : 'ol';
      if (lista !== deseada) {
        cerrarLista();
        salida.push(deseada === 'ul'
          ? '<ul class="list-disc space-y-1 pl-5">'
          : '<ol class="list-decimal space-y-1 pl-5">');
        lista = deseada;
      }
      const contenido = (vineta ? vineta[1] : numerada[1]);
      salida.push(`<li>${enLinea(contenido)}</li>`);
      continue;
    }

    // --- Párrafo -------------------------------------------------------
    cerrarTodo();
    salida.push(`<p class="py-0.5">${enLinea(l)}</p>`);
  }

  cerrarTodo();
  return salida.join('');
}

// ============================================================================
// Utilidades: datos de prueba
// ============================================================================

async function cargarDemo(recrear) {
  try {
    const resultado = await api('/api/datos-demo', {
      method: 'POST', body: { recrear },
    });
    const cuadre = resultado.cuadre_libro_diario;
    UI.toast({
      variant: 'success',
      title: 'Datos de prueba listos',
      description: `${resultado.cuentas_creadas} cuentas y ${resultado.asientos_creados} `
        + `asientos cargados. Total Debe ${fmt(cuadre.total_debe)} = Total Haber ${fmt(cuadre.total_haber)}.`,
    });
    await Promise.all([cargarEmpresa(), cargarCuentas(), cargarPanel(), cargarDiario()]);
  } catch (error) {
    avisarError(error);
  }
}

// ============================================================================
// Arranque de la página
// ============================================================================

// ============================================================================
// Acceso: cuenta de empresa o uso individual
// ============================================================================
//
// Es una pantalla de demostración: no hay usuarios en el servidor. La cuenta
// de empresa se guarda en `localStorage` (con la contraseña como huella
// SHA-256 y una sal) y la sesión en `sessionStorage`, que se borra al cerrar
// la pestaña. El modo individual es fijo: usuario Franco, contraseña Franco.
//
// La empresa es única por instalación: al entrar se copian sus datos a la
// tabla `empresa`, que es la que usa el ERP en la barra y en el Panel.

/** Usuario y contraseña del modo individual (sin distinguir mayúsculas). */
const USUARIO_INDIVIDUAL = 'Franco';
const CLAVE_INDIVIDUAL = 'Franco';

/** Claves de almacenamiento: sesión (pestaña) y cuenta de empresa (navegador). */
const CLAVE_SESION = 'erp-sesion';
const CLAVE_CUENTA_EMPRESA = 'erp-cuenta-empresa';

/** Departamentos de Paraguay (más Asunción, que es distrito capital). */
const DEPARTAMENTOS = [
  'Asunción', 'Concepción', 'San Pedro', 'Cordillera', 'Guairá', 'Caaguazú',
  'Caazapá', 'Itapúa', 'Misiones', 'Paraguarí', 'Alto Paraná', 'Central',
  'Ñeembucú', 'Amambay', 'Canindeyú', 'Presidente Hayes', 'Boquerón',
  'Alto Paraguay',
];

/**
 * Figuras jurídicas que existen en Paraguay, con los datos propios de cada
 * una. Los campos comunes (razón social, RUC, actividad, ejercicio, moneda,
 * usuario y contraseña) se piden siempre; acá van solo los adicionales.
 */
const TIPOS_EMPRESA = [
  {
    id: 'unipersonal',
    nombre: 'Empresa unipersonal (persona física)',
    campos: [
      { clave: 'titular', etiqueta: 'Nombre y apellido del titular', tipo: 'text', requerido: true, ancho: true },
      { clave: 'cedula', etiqueta: 'Cédula de identidad', tipo: 'text', inputmode: 'numeric' },
    ],
  },
  {
    id: 'sa',
    nombre: 'Sociedad Anónima (S.A.)',
    campos: [
      { clave: 'capital', etiqueta: 'Capital social suscripto (G.)', tipo: 'text', inputmode: 'numeric' },
      { clave: 'accionistas', etiqueta: 'Cantidad de accionistas', tipo: 'text', inputmode: 'numeric' },
      { clave: 'presidente', etiqueta: 'Presidente del directorio', tipo: 'text', ancho: true },
    ],
  },
  {
    id: 'srl',
    nombre: 'Sociedad de Responsabilidad Limitada (S.R.L.)',
    campos: [
      { clave: 'capital', etiqueta: 'Capital social (G.)', tipo: 'text', inputmode: 'numeric' },
      { clave: 'socios', etiqueta: 'Cantidad de socios (2 a 25)', tipo: 'text', inputmode: 'numeric' },
      { clave: 'gerente', etiqueta: 'Gerente o administrador', tipo: 'text', ancho: true },
    ],
  },
  {
    id: 'eas',
    nombre: 'Empresa por Acciones Simplificada (E.A.S.)',
    campos: [
      { clave: 'capital', etiqueta: 'Capital social (G.)', tipo: 'text', inputmode: 'numeric' },
      { clave: 'accionistaUnico', etiqueta: 'Accionista único', tipo: 'text' },
    ],
  },
  {
    id: 'colectiva',
    nombre: 'Sociedad Colectiva',
    campos: [
      { clave: 'socios', etiqueta: 'Cantidad de socios', tipo: 'text', inputmode: 'numeric' },
      { clave: 'sociosNombres', etiqueta: 'Socios (nombres separados por coma)', tipo: 'text', ancho: true },
    ],
  },
  {
    id: 'comandita_simple',
    nombre: 'Sociedad en Comandita Simple',
    campos: [
      { clave: 'comanditados', etiqueta: 'Socios comanditados', tipo: 'text', inputmode: 'numeric' },
      { clave: 'comanditarios', etiqueta: 'Socios comanditarios', tipo: 'text', inputmode: 'numeric' },
    ],
  },
  {
    id: 'comandita_acciones',
    nombre: 'Sociedad en Comandita por Acciones',
    campos: [
      { clave: 'capital', etiqueta: 'Capital social (G.)', tipo: 'text', inputmode: 'numeric' },
      { clave: 'comanditados', etiqueta: 'Socios comanditados', tipo: 'text', inputmode: 'numeric' },
      { clave: 'comanditarios', etiqueta: 'Socios comanditarios', tipo: 'text', inputmode: 'numeric' },
    ],
  },
  {
    id: 'capital_industria',
    nombre: 'Sociedad de Capital e Industria',
    campos: [
      { clave: 'sociosCapitalistas', etiqueta: 'Socios capitalistas', tipo: 'text', inputmode: 'numeric' },
      { clave: 'sociosIndustriales', etiqueta: 'Socios industriales', tipo: 'text', inputmode: 'numeric' },
    ],
  },
  {
    id: 'accidental',
    nombre: 'Sociedad accidental (cuentas en participación)',
    campos: [
      { clave: 'gestor', etiqueta: 'Socio gestor', tipo: 'text' },
      { clave: 'participes', etiqueta: 'Cantidad de partícipes', tipo: 'text', inputmode: 'numeric' },
    ],
  },
  {
    id: 'cooperativa',
    nombre: 'Cooperativa',
    campos: [
      { clave: 'registroIncoop', etiqueta: 'Registro en el INCOOP', tipo: 'text' },
      { clave: 'asociados', etiqueta: 'Cantidad de asociados', tipo: 'text', inputmode: 'numeric' },
      { clave: 'consejo', etiqueta: 'Consejo de administración', tipo: 'text', ancho: true },
    ],
  },
  {
    id: 'asociacion',
    nombre: 'Asociación sin fines de lucro',
    campos: [
      { clave: 'personeria', etiqueta: 'Personería jurídica (resolución)', tipo: 'text' },
      { clave: 'fin', etiqueta: 'Fin de la asociación', tipo: 'text', ancho: true },
    ],
  },
  {
    id: 'fundacion',
    nombre: 'Fundación',
    campos: [
      { clave: 'personeria', etiqueta: 'Personería jurídica (resolución)', tipo: 'text' },
      { clave: 'fin', etiqueta: 'Fin de la fundación', tipo: 'text', ancho: true },
    ],
  },
  {
    id: 'sucursal',
    nombre: 'Sucursal de sociedad extranjera',
    campos: [
      { clave: 'casaMatriz', etiqueta: 'Casa matriz', tipo: 'text', ancho: true },
      { clave: 'paisOrigen', etiqueta: 'País de origen', tipo: 'text' },
      { clave: 'capitalAsignado', etiqueta: 'Capital asignado (G.)', tipo: 'text', inputmode: 'numeric' },
    ],
  },
  {
    id: 'consorcio',
    nombre: 'Consorcio / Unión Transitoria de Empresas (UTE)',
    campos: [
      { clave: 'integrantes', etiqueta: 'Empresas integrantes', tipo: 'text', ancho: true },
      { clave: 'objeto', etiqueta: 'Objeto del consorcio', tipo: 'text', ancho: true },
    ],
  },
  {
    id: 'economia_mixta',
    nombre: 'Sociedad de Economía Mixta (SEM)',
    campos: [
      { clave: 'participacionEstatal', etiqueta: 'Participación estatal (%)', tipo: 'text', inputmode: 'numeric' },
      { clave: 'sociosPrivados', etiqueta: 'Socios privados', tipo: 'text', inputmode: 'numeric' },
    ],
  },
];

/** Lee un objeto JSON de un almacenamiento, sin romper si está corrupto. */
function leerJson(clave, almacen) {
  try {
    const crudo = almacen.getItem(clave);
    return crudo ? JSON.parse(crudo) : null;
  } catch {
    return null;
  }
}

/** Cuenta de empresa guardada en este navegador, o null. */
function cuentaEmpresaGuardada() {
  const cuenta = leerJson(CLAVE_CUENTA_EMPRESA, localStorage);
  return cuenta && cuenta.perfil ? cuenta : null;
}

/** Guarda la cuenta de empresa; devuelve false si el navegador no la acepta. */
function guardarCuentaEmpresa(cuenta) {
  try {
    localStorage.setItem(CLAVE_CUENTA_EMPRESA, JSON.stringify(cuenta));
    return true;
  } catch {
    return false;
  }
}

/**
 * Sesión actual. Las sesiones viejas guardaban solo el nombre, así que se
 * interpretan como modo individual para no dejar a nadie afuera.
 */
function leerSesion() {
  try {
    const crudo = sessionStorage.getItem(CLAVE_SESION);
    if (!crudo) return null;
    if (crudo.startsWith('{')) {
      const datos = JSON.parse(crudo);
      return datos && datos.usuario ? datos : null;
    }
    return { modo: 'individual', usuario: crudo };
  } catch {
    return null;
  }
}

/** ¿Ya se inició sesión en esta pestaña? */
function haySesion() {
  return Boolean(leerSesion());
}

/** Nombre del usuario que entró, o cadena vacía si todavía no hay sesión. */
function nombreDeSesion() {
  return leerSesion()?.usuario || '';
}

/** Perfil de la empresa, solo cuando se entró en modo empresa. */
function perfilDeSesion() {
  const sesion = leerSesion();
  return sesion?.modo === 'empresa' ? (sesion.perfil || null) : null;
}

function guardarSesion(sesion) {
  try {
    sessionStorage.setItem(CLAVE_SESION, JSON.stringify(sesion));
  } catch {
    // Sin almacenamiento la sesión vale solo para esta pantalla.
  }
}

function borrarSesion() {
  try {
    sessionStorage.removeItem(CLAVE_SESION);
  } catch {
    // Nada que limpiar.
  }
}

/** Huella de la contraseña. SHA-256 cuando el navegador lo permite. */
async function huellaClave(clave, sal) {
  const texto = `${sal}|${clave}`;
  if (globalThis.crypto?.subtle) {
    const bytes = new TextEncoder().encode(texto);
    const resumen = await crypto.subtle.digest('SHA-256', bytes);
    const hex = [...new Uint8Array(resumen)]
      .map((b) => b.toString(16).padStart(2, '0')).join('');
    return `sha256:${hex}`;
  }
  // Respaldo para contextos sin `crypto.subtle` (por ejemplo, http en una red
  // local). No es criptográfico: la pantalla es una demostración.
  let hash = 2166136261;
  for (let i = 0; i < texto.length; i += 1) {
    hash ^= texto.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return `fnv:${(hash >>> 0).toString(16)}`;
}

/** Sal aleatoria para la huella de la contraseña. */
function salNueva() {
  if (globalThis.crypto?.getRandomValues) {
    return [...crypto.getRandomValues(new Uint8Array(12))]
      .map((b) => b.toString(16).padStart(2, '0')).join('');
  }
  return `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

/** Pasa los datos del perfil al formato de la tabla `empresa`. */
function datosEmpresaDesdePerfil(perfil) {
  return {
    nombre: perfil.razonSocial || '',
    cuit: perfil.ruc ? `${perfil.ruc}-${perfil.dv || ''}` : '',
    actividad: perfil.actividad || '',
    ejercicio_inicio: perfil.ejercicioInicio || '',
    ejercicio_fin: perfil.ejercicioFin || '',
    moneda: perfil.moneda || 'G.',
  };
}

function mostrarErrorAcceso(caja, mensaje) {
  if (!caja) return;
  caja.textContent = mensaje;
  caja.classList.remove('oculto');
}

function ocultarErrorAcceso(caja) {
  if (!caja) return;
  caja.textContent = '';
  caja.classList.add('oculto');
}

/** Pantalla de acceso: siempre arranca en el selector de modo. */
function pedirAcceso() {
  const pantalla = $('#acceso');
  const aplicacion = $('#aplicacion');
  if (!pantalla) return;

  pantalla.classList.remove('oculto');
  if (aplicacion) aplicacion.classList.add('oculto');

  // Se limpian los formularios para no arrastrar intentos anteriores.
  $('#acceso-formulario-individual')?.reset();
  $('#acceso-formulario-empresa')?.reset();
  ocultarErrorAcceso($('#acceso-individual-error'));
  ocultarErrorAcceso($('#acceso-empresa-error'));
  ocultarErrorAcceso($('#acceso-registro-error'));

  actualizarTarjetaEmpresa();
  mostrarPaso('modos');
  $('#acceso-modo-empresa')?.focus();
}

/**
 * Muestra uno de los pasos del acceso y ajusta el ancho de la tarjeta.
 * Pasos: `modos`, `individual`, `empresa-login` y `registro`.
 */
function mostrarPaso(paso) {
  const pasos = {
    modos: '#acceso-modos',
    individual: '#acceso-formulario-individual',
    'empresa-login': '#acceso-formulario-empresa',
    registro: '#acceso-formulario-registro',
  };
  Object.entries(pasos).forEach(([nombre, selector]) => {
    $(selector)?.classList.toggle('oculto', nombre !== paso);
  });
  $('#acceso-tarjeta')?.classList.toggle('ancha', paso === 'registro');

  const subtitulos = {
    modos: 'Elegí cómo vas a usar el sistema.',
    individual: 'Entrá con el usuario de práctica.',
    'empresa-login': 'Ingresá con la cuenta de tu empresa.',
    registro: 'Completá los datos para crear la cuenta de la empresa.',
  };
  const subtitulo = $('#acceso-subtitulo');
  if (subtitulo) subtitulo.textContent = subtitulos[paso] || '';

  const tarjeta = $('#acceso-tarjeta');
  if (tarjeta) tarjeta.scrollTop = 0;
}

/** Texto de la tarjeta «Empresa»: la cuenta guardada o la invitación. */
function actualizarTarjetaEmpresa() {
  const detalle = $('#acceso-modo-empresa-detalle');
  if (!detalle) return;
  const cuenta = cuentaEmpresaGuardada();
  detalle.textContent = cuenta
    ? `${cuenta.perfil.razonSocial} · ${cuenta.perfil.tipoNombre}`
    : 'S.A., S.R.L., E.A.S., unipersonal, cooperativa y más';
}

function mostrarIdentidadEmpresa(cuenta) {
  const caja = $('#acceso-empresa-identidad');
  if (!caja) return;
  const perfil = cuenta.perfil;
  caja.innerHTML = `
    <p class="text-sm font-semibold text-surface-800">${esc(perfil.razonSocial)}</p>
    <p class="mt-0.5 text-xs text-surface-500">
      ${esc(perfil.tipoNombre)}${perfil.ruc ? ` · RUC ${esc(perfil.ruc)}-${esc(perfil.dv)}` : ''}
    </p>`;
}

/** HTML de un campo del formulario de registración (label + control). */
function htmlCampoAcceso(campo, valor, prefijo) {
  const id = `${prefijo}-${campo.clave}`;
  const control = campo.tipo === 'select'
    ? UI.selectHTML({
        id,
        name: campo.clave,
        items: campo.opciones,
        value: valor ?? '',
        placeholder: 'Elegir…',
      })
    : UI.inputHTML({
        id,
        name: campo.clave,
        type: campo.tipo || 'text',
        value: valor ?? '',
        placeholder: campo.placeholder || '',
        inputmode: campo.inputmode || null,
        min: campo.min ?? null,
        max: campo.max ?? null,
        autocomplete: prefijo === 'acceso-reg' ? 'off' : null,
      });
  return UI.campoHTML({
    id,
    label: campo.etiqueta,
    control,
    isRequired: Boolean(campo.requerido),
    className: campo.ancho ? 'sm:col-span-2' : '',
  });
}

/** Campos comunes de cualquier empresa. */
function camposComunesEmpresa(tipoId) {
  const anio = new Date().getFullYear();
  return [
    {
      clave: 'tipo',
      etiqueta: 'Tipo de empresa',
      tipo: 'select',
      requerido: true,
      ancho: true,
      opciones: TIPOS_EMPRESA.map((tipo) => ({ value: tipo.id, label: tipo.nombre })),
      valor: tipoId,
    },
    {
      clave: 'razonSocial',
      etiqueta: 'Razón social o denominación',
      tipo: 'text',
      requerido: true,
      ancho: true,
      placeholder: 'Ej.: Distribuidora Sur S.R.L.',
    },
    {
      clave: 'ruc',
      etiqueta: 'RUC (sin dígito verificador)',
      tipo: 'text',
      requerido: true,
      inputmode: 'numeric',
      placeholder: '80012345',
    },
    {
      clave: 'dv',
      etiqueta: 'Dígito verificador',
      tipo: 'text',
      requerido: true,
      inputmode: 'numeric',
      placeholder: '6',
    },
    {
      clave: 'actividad',
      etiqueta: 'Actividad principal',
      tipo: 'text',
      requerido: true,
      ancho: true,
      placeholder: 'Comercio mayorista de mercaderías',
    },
    {
      clave: 'departamento',
      etiqueta: 'Departamento',
      tipo: 'select',
      opciones: DEPARTAMENTOS,
    },
    {
      clave: 'ciudad',
      etiqueta: 'Ciudad o distrito',
      tipo: 'text',
      placeholder: 'Ej.: Asunción',
    },
    {
      clave: 'direccion',
      etiqueta: 'Dirección',
      tipo: 'text',
      ancho: true,
    },
    {
      clave: 'telefono',
      etiqueta: 'Teléfono',
      tipo: 'tel',
      placeholder: 'Ej.: 021 123 456',
    },
    {
      clave: 'email',
      etiqueta: 'Correo electrónico',
      tipo: 'email',
      placeholder: 'empresa@ejemplo.com',
    },
    {
      clave: 'representante',
      etiqueta: 'Representante legal',
      tipo: 'text',
    },
    {
      clave: 'ejercicioInicio',
      etiqueta: 'Inicio del ejercicio',
      tipo: 'date',
      requerido: true,
      valor: `${anio}-01-01`,
    },
    {
      clave: 'ejercicioFin',
      etiqueta: 'Fin del ejercicio',
      tipo: 'date',
      requerido: true,
      valor: `${anio}-12-31`,
    },
    {
      clave: 'moneda',
      etiqueta: 'Símbolo de moneda',
      tipo: 'text',
      valor: 'G.',
    },
  ];
}

/** Dibuja el formulario de registración completo, según el tipo elegido. */
function pintarRegistro(tipoId = 'srl') {
  const contenedor = $('#acceso-registro-campos');
  if (!contenedor) return;

  contenedor.innerHTML = camposComunesEmpresa(tipoId)
    .map((campo) => htmlCampoAcceso(campo, campo.valor, 'acceso-reg'))
    .join('')
    + '<div id="acceso-registro-extras" class="sm:col-span-2 grid gap-3 sm:grid-cols-2"></div>';

  pintarExtrasTipo(tipoId);
  const selector = $('#acceso-reg-tipo');
  if (selector) selector.onchange = () => pintarExtrasTipo(selector.value);
}

/** Campos propios de la figura jurídica elegida. */
function pintarExtrasTipo(tipoId) {
  const caja = $('#acceso-registro-extras');
  if (!caja) return;
  const tipo = TIPOS_EMPRESA.find((t) => t.id === tipoId) || TIPOS_EMPRESA[0];
  caja.innerHTML = `
    <div class="mt-1 border-t border-surface-200 pt-3 sm:col-span-2">
      <p class="text-xs font-semibold tracking-wide text-surface-500 uppercase">
        Datos propios de ${esc(tipo.nombre)}
      </p>
    </div>
    ${tipo.campos.map((campo) => htmlCampoAcceso(campo, '', 'acceso-extra')).join('')}`;
}

function abrirRegistro() {
  pintarRegistro('srl');
  ['#acceso-registro-usuario', '#acceso-registro-clave', '#acceso-registro-clave2']
    .forEach((selector) => {
      const campo = $(selector);
      if (campo) campo.value = '';
    });
  ocultarErrorAcceso($('#acceso-registro-error'));

  const aviso = $('#acceso-registro-aviso');
  const cuenta = cuentaEmpresaGuardada();
  if (aviso) {
    aviso.textContent = cuenta
      ? `Ya hay una cuenta guardada (${cuenta.perfil.razonSocial}). Al crear esta, `
        + 'se reemplaza. Los datos contables que ya existan no se borran: para '
        + 'empezar de cero, usá «Reiniciar datos» en el Panel.'
      : 'La cuenta se guarda en este navegador; la contraseña queda como huella '
        + 'y no se envía al servidor.';
  }

  mostrarPaso('registro');
  $('#acceso-reg-razonSocial')?.focus();
}

/** Valida el formulario y devuelve el perfil listo para guardar. */
function leerRegistro() {
  const formulario = $('#acceso-formulario-registro');
  const datos = formulario ? Object.fromEntries(new FormData(formulario).entries()) : {};
  const tipo = TIPOS_EMPRESA.find((t) => t.id === datos.tipo) || TIPOS_EMPRESA[0];

  const texto = (valor) => String(valor ?? '').trim();
  const extras = {};
  tipo.campos.forEach((campo) => {
    extras[campo.clave] = texto(datos[campo.clave]);
  });

  const perfil = {
    tipo: tipo.id,
    tipoNombre: tipo.nombre,
    razonSocial: texto(datos.razonSocial),
    ruc: texto(datos.ruc).replace(/\D/g, ''),
    dv: texto(datos.dv).replace(/\D/g, '').slice(0, 1),
    actividad: texto(datos.actividad),
    departamento: texto(datos.departamento),
    ciudad: texto(datos.ciudad),
    direccion: texto(datos.direccion),
    telefono: texto(datos.telefono),
    email: texto(datos.email),
    representante: texto(datos.representante),
    ejercicioInicio: texto(datos.ejercicioInicio),
    ejercicioFin: texto(datos.ejercicioFin),
    moneda: texto(datos.moneda) || 'G.',
    extras,
    creada: new Date().toISOString(),
  };
  return { datos, perfil, tipo };
}

function validarRegistro(datos, perfil, tipo) {
  if (!perfil.razonSocial) return 'Escribí la razón social o denominación.';
  if (!/^\d{6,9}$/.test(perfil.ruc)) {
    return 'El RUC debe tener entre 6 y 9 dígitos, sin puntos ni guiones.';
  }
  if (!/^\d$/.test(perfil.dv)) return 'El dígito verificador es un solo número.';
  if (!perfil.actividad) return 'Escribí la actividad principal.';
  if (!perfil.ejercicioInicio || !perfil.ejercicioFin) {
    return 'Completá el inicio y el fin del ejercicio.';
  }
  if (perfil.ejercicioInicio > perfil.ejercicioFin) {
    return 'El ejercicio no puede terminar antes de empezar.';
  }
  if (perfil.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(perfil.email)) {
    return 'El correo electrónico no parece válido.';
  }
  const faltante = tipo.campos.find((campo) => campo.requerido && !perfil.extras[campo.clave]);
  if (faltante) return `Completá «${faltante.etiqueta}».`;

  const usuario = String(datos.usuario || '').trim();
  if (usuario.length < 3) return 'El usuario necesita al menos 3 caracteres.';
  if (usuario.includes(' ')) return 'El usuario no puede tener espacios.';
  if (String(datos.clave || '').length < 4) {
    return 'La contraseña necesita al menos 4 caracteres.';
  }
  if (datos.clave !== datos.clave2) return 'Las contraseñas no coinciden.';
  return '';
}

/**
 * Copia los datos de la empresa al servidor y entra al ERP.
 * Si la API falla, la cuenta ya quedó guardada y se puede reintentar.
 */
async function entrarConEmpresa(cuenta, cajaError, guardarCuenta = false) {
  try {
    await api('/api/empresa', {
      method: 'PUT',
      body: datosEmpresaDesdePerfil(cuenta.perfil),
    });
  } catch (error) {
    mostrarErrorAcceso(
      cajaError,
      `No se pudo guardar la empresa en el servidor: ${error.mensaje || 'error de conexión'}.`,
    );
    return false;
  }

  // La cuenta recién se guarda cuando el servidor aceptó los datos: si falla,
  // la cuenta anterior (si existía) queda intacta.
  if (guardarCuenta && !guardarCuentaEmpresa(cuenta)) {
    mostrarErrorAcceso(cajaError, 'Este navegador no permite guardar la cuenta. '
      + 'Revisá el almacenamiento local y volvé a intentar.');
    return false;
  }

  guardarSesion({ modo: 'empresa', usuario: cuenta.usuario, perfil: cuenta.perfil });
  entrarAlSistema();
  return true;
}

function entrarAlSistema() {
  const sesion = leerSesion();
  $('#acceso')?.classList.add('oculto');
  $('#aplicacion')?.classList.remove('oculto');
  // Recién acá arranca el ERP: mientras no haya sesión, no se pide nada a la
  // API ni se dibuja la interfaz.
  iniciar();
  recibirConPiraPires(sesion?.usuario || '');
}

/** Cierra la sesión y vuelve a la pantalla de acceso. */
function cerrarSesion() {
  borrarSesion();
  const campo = $('#acceso-usuario');
  if (campo) campo.value = '';
  const clave = $('#acceso-clave');
  if (clave) clave.value = '';
  pedirAcceso();
}

/**
 * Sincroniza la cuenta guardada cuando se editan los datos de la empresa
 * desde el Panel, para que el próximo acceso vuelva a dejar todo igual.
 */
function actualizarCuentaEmpresa(datos) {
  const cuenta = cuentaEmpresaGuardada();
  if (!cuenta) return;
  const perfil = { ...cuenta.perfil };

  const texto = (valor) => String(valor ?? '').trim();
  if (texto(datos.nombre)) perfil.razonSocial = texto(datos.nombre);
  if (texto(datos.actividad)) perfil.actividad = texto(datos.actividad);
  if (texto(datos.ejercicio_inicio)) perfil.ejercicioInicio = texto(datos.ejercicio_inicio);
  if (texto(datos.ejercicio_fin)) perfil.ejercicioFin = texto(datos.ejercicio_fin);
  if (texto(datos.moneda)) perfil.moneda = texto(datos.moneda);
  if (texto(datos.cuit)) {
    const [ruc, dv] = texto(datos.cuit).split('-');
    perfil.ruc = texto(ruc).replace(/\D/g, '');
    perfil.dv = texto(dv).replace(/\D/g, '').slice(0, 1);
  }

  cuenta.perfil = perfil;
  guardarCuentaEmpresa(cuenta);

  const sesion = leerSesion();
  if (sesion?.modo === 'empresa') {
    sesion.perfil = perfil;
    guardarSesion(sesion);
  }
}

/** Tarjeta del Panel con los datos cargados al crear la cuenta de empresa. */
function pintarPerfilEmpresa() {
  const caja = $('#panel-perfil-empresa');
  const contenido = $('#panel-perfil-empresa-contenido');
  if (!caja || !contenido) return;

  const perfil = perfilDeSesion();
  if (!perfil) {
    caja.classList.add('oculto');
    contenido.innerHTML = '';
    return;
  }

  const tipo = TIPOS_EMPRESA.find((t) => t.id === perfil.tipo);
  const filas = [
    ['Tipo de empresa', perfil.tipoNombre || tipo?.nombre],
    ['RUC', perfil.ruc && `${perfil.ruc}-${perfil.dv || ''}`],
    ['Departamento', perfil.departamento],
    ['Ciudad o distrito', perfil.ciudad],
    ['Dirección', perfil.direccion],
    ['Teléfono', perfil.telefono],
    ['Correo electrónico', perfil.email],
    ['Representante legal', perfil.representante],
    ...(tipo?.campos || []).map((campo) => [campo.etiqueta, perfil.extras?.[campo.clave]]),
  ].filter(([, valor]) => String(valor ?? '').trim());

  contenido.innerHTML = filas.length
    ? `<dl class="grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">${filas.map(([etiqueta, valor]) => `
        <div>
          <dt class="text-xs text-surface-500">${esc(etiqueta)}</dt>
          <dd class="mt-0.5 text-sm font-medium text-surface-800">${esc(valor)}</dd>
        </div>`).join('')}</dl>`
    : '<p class="text-sm text-surface-500">La cuenta no tiene datos adicionales.</p>';
  caja.classList.remove('oculto');
}

/** Conecta los formularios y los botones de la pantalla de acceso. */
function conectarAcceso() {
  $('#acceso-modo-empresa')?.addEventListener('click', () => {
    const cuenta = cuentaEmpresaGuardada();
    if (cuenta) {
      mostrarIdentidadEmpresa(cuenta);
      mostrarPaso('empresa-login');
      ocultarErrorAcceso($('#acceso-empresa-error'));
      const usuario = $('#acceso-empresa-usuario');
      if (usuario) {
        usuario.value = cuenta.usuario;
        usuario.focus();
        usuario.select();
      }
      $('#acceso-empresa-clave')?.focus();
    } else {
      abrirRegistro();
    }
  });

  $('#acceso-modo-individual')?.addEventListener('click', () => {
    mostrarPaso('individual');
    ocultarErrorAcceso($('#acceso-individual-error'));
    const usuario = $('#acceso-usuario');
    if (usuario) {
      usuario.value = '';
      usuario.focus();
    }
    const clave = $('#acceso-clave');
    if (clave) clave.value = '';
  });

  $('#acceso-individual-volver')?.addEventListener('click', () => {
    actualizarTarjetaEmpresa();
    mostrarPaso('modos');
    $('#acceso-modo-empresa')?.focus();
  });
  $('#acceso-empresa-volver')?.addEventListener('click', () => {
    actualizarTarjetaEmpresa();
    mostrarPaso('modos');
    $('#acceso-modo-empresa')?.focus();
  });
  $('#acceso-registro-volver')?.addEventListener('click', () => {
    actualizarTarjetaEmpresa();
    mostrarPaso('modos');
    $('#acceso-modo-empresa')?.focus();
  });
  $('#acceso-empresa-crear')?.addEventListener('click', abrirRegistro);

  // Los errores se limpian al corregir el campo que los provocó.
  ['#acceso-usuario', '#acceso-clave'].forEach((selector) => {
    $(selector)?.addEventListener('input', () => ocultarErrorAcceso($('#acceso-individual-error')));
  });
  ['#acceso-empresa-usuario', '#acceso-empresa-clave'].forEach((selector) => {
    $(selector)?.addEventListener('input', () => ocultarErrorAcceso($('#acceso-empresa-error')));
  });

  // --- Modo individual: Franco / Franco --------------------------------
  $('#acceso-formulario-individual')?.addEventListener('submit', (evento) => {
    evento.preventDefault();
    const cajaError = $('#acceso-individual-error');
    const usuario = String($('#acceso-usuario')?.value || '').trim();
    const clave = String($('#acceso-clave')?.value || '');
    if (!usuario || !clave) {
      mostrarErrorAcceso(cajaError, 'Completá el usuario y la contraseña.');
      return;
    }
    if (usuario.toLowerCase() !== USUARIO_INDIVIDUAL.toLowerCase()
        || clave.toLowerCase() !== CLAVE_INDIVIDUAL.toLowerCase()) {
      mostrarErrorAcceso(cajaError, 'Usuario o contraseña incorrectos. '
        + 'En el modo individual es Franco / Franco.');
      return;
    }
    ocultarErrorAcceso(cajaError);
    guardarSesion({ modo: 'individual', usuario: USUARIO_INDIVIDUAL });
    entrarAlSistema();
  });

  // --- Empresa: ingresar con la cuenta guardada -----------------------
  $('#acceso-formulario-empresa')?.addEventListener('submit', async (evento) => {
    evento.preventDefault();
    const cajaError = $('#acceso-empresa-error');
    const cuenta = cuentaEmpresaGuardada();
    if (!cuenta) {
      abrirRegistro();
      return;
    }

    const usuario = String($('#acceso-empresa-usuario')?.value || '').trim();
    const clave = String($('#acceso-empresa-clave')?.value || '');
    if (!usuario || !clave) {
      mostrarErrorAcceso(cajaError, 'Completá el usuario y la contraseña.');
      return;
    }
    ocultarErrorAcceso(cajaError);

    const boton = $('#acceso-empresa-entrar');
    boton?.classList.add('btn-pending');
    try {
      const huella = await huellaClave(clave, cuenta.sal);
      const usuarioOk = usuario.toLowerCase() === String(cuenta.usuario).toLowerCase();
      if (!usuarioOk || huella !== cuenta.huella) {
        mostrarErrorAcceso(cajaError, 'Usuario o contraseña incorrectos.');
        return;
      }
      await entrarConEmpresa(cuenta, cajaError);
    } finally {
      boton?.classList.remove('btn-pending');
    }
  });

  // --- Empresa: crear cuenta ------------------------------------------
  $('#acceso-formulario-registro')?.addEventListener('submit', async (evento) => {
    evento.preventDefault();
    const cajaError = $('#acceso-registro-error');
    const { datos, perfil, tipo } = leerRegistro();
    const problema = validarRegistro(datos, perfil, tipo);
    if (problema) {
      mostrarErrorAcceso(cajaError, problema);
      return;
    }
    ocultarErrorAcceso(cajaError);

    const usuario = String(datos.usuario || '').trim();
    const boton = $('#acceso-registro-crear');
    boton?.classList.add('btn-pending');
    try {
      const sal = salNueva();
      const cuenta = {
        version: 1,
        usuario,
        sal,
        huella: await huellaClave(String(datos.clave || ''), sal),
        perfil,
      };
      await entrarConEmpresa(cuenta, cajaError, true);
    } finally {
      boton?.classList.remove('btn-pending');
    }
  });
}

/**
 * Da la bienvenida con Pira Pires al entrar.
 *
 * Abre el panel flotante con un saludo personalizado que se escribe solo, con
 * la misma animación que las respuestas. No se le pregunta a la IA: el saludo
 * es fijo, así entrar no depende de que haya internet ni gasta una consulta.
 */
function recibirConPiraPires(nombre) {
  if (!piraAbierto) alternarPira(true);

  const contenedor = $('#pira-historial');
  if (!contenedor) return;

  // Se limpia el historial para que la bienvenida sea lo primero que se ve.
  contenedor.innerHTML = '';

  const saludo = `¡Hola, ${nombre}! Soy **Pira Pires**, tu asistente contable.\n\n`
    + 'Puedo ayudarte con la contabilidad y los impuestos de Paraguay: '
    + 'el IVA (Ley N.º 1259/97), la liquidación mensual, la SET y el DNIT.\n\n'
    + '¿Por dónde querés empezar?';

  // El saludo se muestra con el avatar de la boca abierta, como si hablara.
  marcarPiraHablando(true);
  const burbuja = agregarBurbuja('assistant', '', null, {
    contenedor, flotante: true,
  });
  animarEscritura(burbuja.cuerpo, markdownBasico(saludo), {
    alEscribir: () => bajarAlFinal(contenedor),
    alTerminar: () => marcarPiraHablando(false),
  });
}

document.addEventListener('DOMContentLoaded', () => {
  $('#btn-cargar-demo').onclick = async () => {
    const aceptado = await UI.confirmar({
      titulo: 'Cargar los datos de prueba',
      descripcion: 'Se cargan las 21 cuentas del plan mínimo del enunciado y las 15 '
        + 'operaciones de la empresa ficticia. Los asientos y cuentas actuales se borran.',
      confirmarTexto: 'Cargar datos',
      icono: 'descargar',
    });
    if (aceptado) cargarDemo(true);
  };

  $('#btn-reiniciar').onclick = async () => {
    const aceptado = await UI.confirmar({
      titulo: 'Vaciar la base de datos',
      descripcion: 'Esto borra TODOS los asientos y todas las cuentas. No se puede deshacer.',
      confirmarTexto: 'Vaciar todo',
      icono: 'papelera',
    });
    if (!aceptado) return;
    try {
      await api('/api/reiniciar', { method: 'POST' });
      avisar('Base de datos vaciada.', 'success');
      await Promise.all([cargarEmpresa(), cargarCuentas(), cargarPanel()]);
    } catch (error) {
      avisarError(error);
    }
  };

  $('#cuenta-busqueda').oninput = debounce(cargarCuentas, 300);
  $('#cuenta-inactivas').onchange = cargarCuentas;
  $('#diario-estado').onchange = cargarDiario;
  $('#diario-busqueda').oninput = debounce(cargarDiario, 300);
  $('#btn-nuevo-asiento').onclick = () => {
    $('#diario-detalle').classList.add('oculto');
    iniciarFormulario();
    $('#form-asiento').scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  $('#btn-cancelar-asiento').onclick = () => iniciarFormulario();

  $('#btn-ver-mayor').onclick = () => cargarMayor(false);
  $('#btn-mayor-todas').onclick = () => cargarMayor(true);
  $('#btn-ver-balance').onclick = cargarBalance;
  $('#balance-vacias').onchange = cargarBalance;
  $('#balance-agrupado').onchange = cargarBalance;

  $$('[data-accion="imprimir"]').forEach((boton) => {
    boton.onclick = () => window.print();
  });

  const botonSalir = $('#btn-salir');
  if (botonSalir) botonSalir.onclick = cerrarSesion;

  // El tema se aplica antes de mostrar nada, así la pantalla de acceso ya sale
  // con el tema elegido y no hay un destello claro.
  iniciarTema();

  // Los formularios de acceso se conectan una sola vez; después solo se
  // muestran y se ocultan según el paso.
  conectarAcceso();

  // Si ya se entró en esta pestaña, se arranca directo; si no, primero la
  // pantalla de acceso. El ERP no se carga hasta que haya sesión.
  if (haySesion()) {
    try {
      $('#acceso')?.classList.add('oculto');
      $('#aplicacion')?.classList.remove('oculto');
    } catch {
      // Nada que ocultar.
    }
    iniciar();
  } else {
    pedirAcceso();
  }
});