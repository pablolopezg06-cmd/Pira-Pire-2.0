/* =============================================================================
 * ui.js — Biblioteca de componentes con la API de TailGrids
 * =============================================================================
 * TailGrids UI v3 publica sus componentes para React con React Aria. En este
 * proyecto no hay Node ni npm, así que no se pueden instalar esos paquetes.
 * Este archivo reproduce el mismo contrato de props y los mismos patrones de
 * accesibilidad sobre HTML y Tailwind CSS v4:
 *
 *   · Button      variant · appearance · size · iconOnly · disabled · pending · onPress
 *   · Badge       variant · appearance · size
 *   · Field       label · description · errorMessage · isRequired
 *   · Dialog      isOpen · title · description · onClose  (foco atrapado, Esc)
 *   · Toast       variant · title · description
 *   · Tabs        keyboard: flechas, Home/End  (role="tablist" / "tab")
 *
 * Lo que en React Aria viene gratis (foco, teclado, aria-*) está escrito a mano
 * en `abrirDialogo` y `conectarTabs`, que son las dos piezas donde importa.
 * ========================================================================== */

const UI = (() => {

  /* ==========================================================================
   * ICONOS
   * SVG inline de 24x24 con `currentColor`, así heredan el color del botón.
   * ====================================================================== */

  const ICONOS = {
    mas:      '<path d="M12 5v14M5 12h14"/>',
    lapiz:    '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    papelera:'<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/>',
    ojo:      '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    tilde:    '<path d="M20 6 9 17l-5-5"/>',
    equis:    '<path d="M18 6 6 18M6 6l12 12"/>',
    refrescar:'<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    impresora:'<path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 14h12v8H6z"/>',
    play:     '<path d="m5 3 14 9-14 9Z"/>',
    prohibido:'<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>',
    alerta:   '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    info:     '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
    cheque:   '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    cruz:     '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>',
    candado:  '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    balanza:  '<path d="M12 3v18M7 21h10"/><path d="M3 8h6l-3 6a3 3 0 0 1-6 0Zm12 0h6l-3 6a3 3 0 0 1-6 0Z"/>',
    descargar:'<path d="M12 3v12"/><path d="m7 11 5 5 5-5"/><path d="M4 20h16"/>',
    archivo:  '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/>',
    // Sol y luna para el botón de tema claro/oscuro.
    sol:      '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    luna:     '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>',
  };

  /**
   * Devuelve el markup de un icono.
   * @param {string} nombre  clave de ICONOS
   * @param {object} opciones { tamano: 16|18|20, clase: string }
   */
  function icono(nombre, opciones = {}) {
    const camino = ICONOS[nombre];
    if (!camino) return '';
    const tamano = opciones.tamano ?? 16;
    const relleno = nombre === 'play' || nombre === 'descargar';
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
      width="${tamano}" height="${tamano}"
      class="${opciones.clase ?? ''}"
      aria-hidden="true" focusable="false"
      fill="${relleno ? 'currentColor' : 'none'}"
      stroke="currentColor" stroke-width="1.8"
      stroke-linecap="round" stroke-linejoin="round">${camino}</svg>`;
  }

  /* ==========================================================================
   * BUTTON  ·  variant · appearance · size · iconOnly · pending · onPress
   * ====================================================================== */

  const VARIANTES_BOTON = ['primary', 'danger', 'success', 'warning', 'secondary', 'ghost'];
  const APARIENCIAS_BOTON = ['fill', 'outline'];
  const TAMANOS_BOTON = ['xs', 'sm', 'md', 'lg'];

  /**
   * Compone las clases de un botón. La API es la de TailGrids:
   * `variant` y `appearance` se combinan libremente (danger + outline).
   */
  function clasesBoton(props = {}) {
    const {
      variant = 'primary',
      appearance = 'fill',
      size = 'md',
      iconOnly = false,
      className = '',
    } = props;

    const v = VARIANTES_BOTON.includes(variant) ? variant : 'primary';
    const a = APARIENCIAS_BOTON.includes(appearance) ? appearance : 'fill';
    const s = TAMANOS_BOTON.includes(size) ? size : 'md';

    // En TailGrids el color del botón lo aporta solo la combinación
    // variant + appearance; no hay una clase intermedia `btn-<variant>`
    // que aplicar. El anillo de foco viene de la capa base.
    return [
      'btn',
      `btn-${v}-${a}`,
      `btn-${s}`,
      iconOnly ? 'btn-icon' : '',
      className,
    ].filter(Boolean).join(' ');
  }

  /**
   * Crea un <button> real (no un string) para poder colgarle listeners.
   *
   * @param {object} opciones
   *   label      texto del botón
   *   icono      nombre del icono (o string SVG)
   *   onPress    callback; se dispara con clic, Enter y Espacio
   *   pending    estado de carga: muestra spinner, marca aria-busy y
   *              conserva el foco (no se deshabilita el elemento)
   *   disabled   deshabilita la interacción y aplica pointer-events-none
   *   readOnly   evita la acción sin quitar el foco
   */
  function crearBoton(opciones = {}) {
    const {
      label = '',
      variant, appearance, size, iconOnly, className,
      title = null,
      onPress = null,
      pending = false,
      disabled = false,
      readOnly = false,
      tipo = 'button',
    } = opciones;

    const boton = document.createElement('button');
    boton.type = tipo;
    boton.className = clasesBoton({ variant, appearance, size, iconOnly, className });

    if (title) boton.title = title;
    if (disabled) boton.disabled = true;
    if (pending) {
      boton.classList.add('btn-pending');
      boton.setAttribute('aria-busy', 'true');
    }
    if (readOnly) boton.setAttribute('aria-readonly', 'true');

    // El contenido va envuelto para que `pending` pueda atenuarlo sin
    // desarmar el botón.
    const contenido = document.createElement('span');
    contenido.className = 'btn-contenido flex items-center gap-2';
    contenido.innerHTML =
      (opciones.icono ? icono(opciones.icono, { tamano: size === 'lg' ? 20 : 16 }) : '')
      + (label ? `<span>${escapar(label)}</span>` : '');
    boton.appendChild(contenido);

    if (pending) {
      const spinner = document.createElement('span');
      spinner.className = 'spinner';
      boton.insertBefore(spinner, contenido);
    }

    if (onPress) {
      // `onPress` reemplaza a `onClick`: los <button> nativos ya disparan
      // click con Enter y Espacio, así que un solo listener alcanza.
      boton.addEventListener('click', (evento) => {
        if (disabled || pending || readOnly) {
          evento.preventDefault();
          return;
        }
        onPress(evento);
      });
    }
    return boton;
  }

  /** Atajo para escribir un botón como HTML dentro de una plantilla. */
  function botonHTML(opciones = {}) {
    const {
      label = '', iconName, variant, appearance, size, iconOnly,
      className, title, attributes = '', pending = false, disabled = false,
    } = opciones;

    const tamano = TAMANOS_BOTON.includes(size) ? size : 'md';
    const svg = iconName ? icono(iconName, { tamano: tamano === 'lg' ? 20 : 16 }) : '';
    // Si quien llama ya indica un `type` (por ejemplo `type="submit"`), no se
    // agrega `type="button"`: con dos atributos `type` el navegador toma el
    // primero y el botón nunca enviaría el formulario.
    const atributosTipo = /\btype\s*=/.test(attributes) ? '' : 'type="button"';
    return `<button ${atributosTipo}
      class="${clasesBoton({ variant, appearance, size, iconOnly, className })}${pending ? ' btn-pending' : ''}"
      ${title ? `title="${escapar(title)}"` : ''}
      ${pending ? 'aria-busy="true"' : ''}
      ${disabled ? 'disabled' : ''}
      ${attributes}><span class="btn-contenido">${svg}${escapar(label)}</span></button>`;
  }

  /* ==========================================================================
   * BADGE  ·  variant · appearance · size
   * ====================================================================== */

  const VARIANTES_BADGE = ['primary', 'success', 'danger', 'warning', 'secondary'];

  /** Clases de un badge. appearance: 'light' (por defecto) o 'solid'. */
  function clasesBadge({ variant = 'secondary', appearance = 'light', className = '' } = {}) {
    const v = VARIANTES_BADGE.includes(variant) ? variant : 'secondary';
    const base = appearance === 'solid' ? `badge-solid-${v}` : `badge-${v}`;
    return ['badge', base, className].filter(Boolean).join(' ');
  }

  /** Badge como string HTML (se usa dentro de plantillas de tablas). */
  function badge(texto, props = {}) {
    const iconoNombre = props.iconName;
    return `<span class="${clasesBadge(props)}">${
      iconoNombre ? icono(iconoNombre, { tamano: 12 }) : ''
    }${escapar(texto)}</span>`;
  }

  /* ==========================================================================
   * CAMPO  ·  label · description · errorMessage · isRequired
   * ====================================================================== */

  /**
   * Devuelve el markup de un campo etiquetado, al estilo del componente Field
   * de TailGrids. `inputHTML` permite pasar el control ya construido.
   */
  function campoHTML(opciones = {}) {
    const {
      id, label, control, description = null,
      errorMessage = null, isRequired = false, className = '',
    } = opciones;

    const idError = errorMessage ? `${id}-error` : null;
    return `
      <div class="${className}">
        <label class="label" for="${id}">
          ${escapar(label)}${isRequired ? ' <span class="required-mark" aria-hidden="true">*</span>' : ''}
        </label>
        ${control}
        ${description ? `<p class="help-text" id="${id}-help">${escapar(description)}</p>` : ''}
        ${errorMessage ? `<p class="error-text" id="${idError}" role="alert">${escapar(errorMessage)}</p>` : ''}
      </div>`;
  }

  /** <input> con las clases del sistema, listo para insertar en una plantilla. */
  function inputHTML(opciones = {}) {
    const {
      id, name = '', type = 'text', value = '', placeholder = '',
      className = '', sm = false, required = false, disabled = false,
      inputmode = null, min = null, max = null, autocomplete = null,
    } = opciones;
    return `<input id="${id}" ${name ? `name="${name}"` : ''} type="${type}"
      value="${escapar(value)}"
      ${placeholder ? `placeholder="${escapar(placeholder)}"` : ''}
      ${inputmode ? `inputmode="${inputmode}"` : ''}
      ${min ? `min="${min}"` : ''} ${max ? `max="${max}"` : ''}
      ${autocomplete ? `autocomplete="${autocomplete}"` : ''}
      ${required ? 'required' : ''} ${disabled ? 'disabled' : ''}
      class="input ${sm ? 'input-sm' : ''} ${className}">`;
  }

  /** <select> con el chevron dibujado en CSS. */
  function selectHTML(opciones = {}) {
    const {
      id, name = '', items = [], value = '', sm = false,
      className = '', placeholder = null, disabled = false,
    } = opciones;

    const opciones_ = (placeholder !== null
      ? `<option value="" ${value === '' ? 'selected' : ''}>${escapar(placeholder)}</option>` : '')
      + items.map((item) => {
          const valor = item.value ?? item;
          const texto = item.label ?? item;
          return `<option value="${escapar(valor)}" ${
            String(valor) === String(value) ? 'selected' : ''}>${escapar(texto)}</option>`;
        }).join('');

    return `<select id="${id}" ${name ? `name="${name}"` : ''}
      ${disabled ? 'disabled' : ''}
      class="select ${sm ? 'input-sm' : ''} ${className}">${opciones_}</select>`;
  }

  /* ==========================================================================
   * ALERT
   * ====================================================================== */

  function alertaHTML(variante, titulo, descripcion = null) {
    const nombreIcono = {
      success: 'cheque', danger: 'cruz', warning: 'alerta', info: 'info',
    }[variante] ?? 'info';
    const colores = {
      success: 'text-success-600', danger: 'text-danger-600',
      warning: 'text-warning-600', info: 'text-primary-600',
    }[variante] ?? 'text-primary-600';

    return `<div class="alert alert-${variante}">
      <span class="${colores} shrink-0">${icono(nombreIcono, { tamano: 18 })}</span>
      <div class="min-w-0">
        <p class="font-medium">${escapar(titulo)}</p>
        ${descripcion ? `<p class="mt-0.5 opacity-90">${descripcion}</p>` : ''}
      </div>
    </div>`;
  }

  /* ==========================================================================
   * TOAST  ·  variant · title · description
   * ====================================================================== */

  const ICONO_TOAST = {
    success: 'cheque', danger: 'cruz', warning: 'alerta', info: 'info',
  };
  const COLOR_TOAST = {
    success: 'text-success-600', danger: 'text-danger-600',
    warning: 'text-warning-600', info: 'text-primary-600',
  };

  /**
   * Muestra un aviso en la región aria-live. No roba el foco: se anuncia, no
   * interrumpe (a diferencia de un alert()).
   */
  function toast(opciones = {}) {
    const {
      variant = 'info', title = '', description = null,
      detalles = [], timeout = null, accion = null,
    } = opciones;

    const region = document.getElementById('avisos');
    if (!region) return null;

    const elemento = document.createElement('div');
    elemento.className = `toast toast-${variant}`;
    elemento.setAttribute('role', variant === 'danger' ? 'alert' : 'status');

    const color = COLOR_TOAST[variant] ?? COLOR_TOAST.info;
    elemento.innerHTML = `
      <span class="${color} shrink-0 mt-0.5">${icono(ICONO_TOAST[variant] ?? 'info', { tamano: 18 })}</span>
      <div class="min-w-0 flex-1">
        <p class="font-medium text-surface-800">${escapar(title)}</p>
        ${description ? `<p class="mt-0.5 break-words text-surface-600">${escapar(description)}</p>` : ''}
        ${detalles.length ? `
          <ul class="mt-1.5 space-y-0.5 text-xs text-surface-500">
            ${detalles.slice(0, 6).map((d) => `<li>· ${escapar(d)}</li>`).join('')}
          </ul>` : ''}
        ${accion ? `<div class="mt-2" data-slot="accion"></div>` : ''}
      </div>
      <button type="button" class="shrink-0 rounded p-1 text-surface-400 transition
                                      hover:bg-surface-100 hover:text-surface-700"
              aria-label="Cerrar aviso">${icono('equis', { tamano: 14 })}</button>`;

    if (accion) {
      const contenedor = elemento.querySelector('[data-slot="accion"]');
      contenedor.appendChild(crearBoton({
        label: accion.label,
        variant: 'primary',
        appearance: 'outline',
        size: 'xs',
        onPress: () => { accion.onPress(); cerrar(); },
      }));
    }

    function cerrar() {
      elemento.remove();
    }

    elemento.querySelector('button[aria-label]').onclick = cerrar;

    region.appendChild(elemento);
    // Los errores quedan más tiempo porque suelen traer varios detalles.
    const espera = timeout ?? (variant === 'danger' ? 12000 : 6000);
    setTimeout(cerrar, espera);
    return elemento;
  }

  /* ==========================================================================
   * DIALOG
   * Es la pieza donde React Aria más trabajo hace, así que está escrita con
   * cuidado: foco atrapado, cierre con Escape, scroll bloqueado, foco
   * devuelto al elemento que abrió el diálogo y roles ARIA correctos.
   * ====================================================================== */

  const FOCALIZABLES = [
    'a[href]', 'button:not([disabled])', 'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
  ].join(',');

  /**
   * Abre un diálogo modal y devuelve una promesa con el resultado.
   *
   * @param {object} opciones
   *   titulo, descripcion        contenido del diálogo
   *   confirmarTexto             texto del botón de acción
   *   cancelarTexto              texto del botón de cancelar (null = sin botón)
   *   variant                    'primary' | 'danger' | 'success'
   *   icono                      icono en la cabecera
   *   campo                      { etiqueta, tipo, valor, placeholder, multilinea, requerido, ayuda }
   *                              si está presente el resultado es el valor escrito
   *   onConfirmar                callback extra; si devuelve false, no se cierra
   * @returns {Promise<false|string|null>}  false/null si se cancela
   */
  function abrirDialogo(opciones = {}) {
    const {
      titulo = '',
      descripcion = null,
      contenido = null,
      confirmarTexto = 'Confirmar',
      cancelarTexto = 'Cancelar',
      variant = 'primary',
      icono: iconoNombre = null,
      campo = null,
      onConfirmar = null,
    } = opciones;

    return new Promise((resolver) => {
      const focoPrevio = document.activeElement;
      const idTitulo = `dlg-titulo-${Date.now()}`;
      const idCuerpo = `dlg-cuerpo-${Date.now()}`;
      const idCampo = `dlg-campo-${Date.now()}`;

      const fondo = document.createElement('div');
      fondo.className = 'dialog-overlay';
      fondo.innerHTML = `
        <div class="dialog" role="dialog" aria-modal="true"
             aria-labelledby="${idTitulo}"${
               (descripcion || contenido) ? `\n             aria-describedby="${idCuerpo}"` : ''}>
          <div class="dialog-header">
            ${iconoNombre ? `<span class="shrink-0 text-surface-500">${icono(iconoNombre, { tamano: 20 })}</span>` : ''}
            <div class="min-w-0">
              <h2 id="${idTitulo}" class="text-base font-semibold text-surface-800">${escapar(titulo)}</h2>
            </div>
          </div>
          <div class="dialog-body space-y-3">
            ${contenido
              ? `<div id="${idCuerpo}" class="text-sm text-surface-600">${contenido}</div>`
              : descripcion
                ? `<p id="${idCuerpo}" class="text-sm text-surface-600">${descripcion}</p>`
                : ''}
            ${campo ? campoHTML({
              id: idCampo,
              label: campo.etiqueta,
              isRequired: Boolean(campo.requerido),
              description: campo.ayuda ?? null,
              control: campo.multilinea
                ? `<textarea id="${idCampo}" class="textarea" rows="3"
                     placeholder="${escapar(campo.placeholder ?? '')}"
                     ${campo.requerido ? 'required' : ''}>${escapar(campo.valor ?? '')}</textarea>`
                : `<input id="${idCampo}" class="input" type="${campo.tipo ?? 'text'}"
                     value="${escapar(campo.valor ?? '')}"
                     placeholder="${escapar(campo.placeholder ?? '')}"
                     ${campo.requerido ? 'required' : ''}>`,
            }) : ''}
          </div>
          <div class="dialog-footer">
            ${cancelarTexto === null ? '' : `<button type="button" class="btn btn-md btn-secondary-outline"
                    data-accion="cancelar">${escapar(cancelarTexto)}</button>`}
            <button type="button"
                    class="btn btn-md btn-${variant}-fill"
                    data-accion="confirmar">${escapar(confirmarTexto)}</button>
          </div>
        </div>`;

      const panel = fondo.querySelector('.dialog');
      const control = fondo.querySelector(`#${CSS.escape(idCampo)}`);
      const botonConfirmar = fondo.querySelector('[data-accion="confirmar"]');
      const botonCancelar = fondo.querySelector('[data-accion="cancelar"]');

      let cerrado = false;
      function cerrar(resultado) {
        if (cerrado) return;
        cerrado = true;
        document.removeEventListener('keydown', alTeclear, true);
        document.body.style.overflow = '';
        fondo.remove();
        // El foco vuelve a donde estaba: se puede seguir tipeando.
        if (focoPrevio && focoPrevio.focus) focoPrevio.focus();
        resolver(resultado);
      }

      function resultado() {
        if (campo) return control.value.trim();
        return true;
      }

      function confirmar() {
        // El HTML required hace la validación; el mensaje va en español.
        if (campo?.requerido && control.value.trim() === '') {
          control.focus();
          control.classList.add('input-error');
          return;
        }
        if (onConfirmar && onConfirmar(control) === false) return;
        cerrar(resultado());
      }

      // --- Foco atrapado: Tab y Shift+Tab circulan dentro del diálogo ---
      function alTeclear(evento) {
        if (evento.key === 'Escape') {
          evento.preventDefault();
          evento.stopPropagation();
          cerrar(campo ? null : false);
          return;
        }
        if (evento.key !== 'Tab') return;

        const focales = Array.from(panel.querySelectorAll(FOCALIZABLES))
          .filter((el) => el.offsetParent !== null || el === document.activeElement);
        if (!focales.length) return;

        const primero = focales[0];
        const ultimo = focales[focales.length - 1];
        if (evento.shiftKey && document.activeElement === primero) {
          evento.preventDefault();
          ultimo.focus();
        } else if (!evento.shiftKey && document.activeElement === ultimo) {
          evento.preventDefault();
          primero.focus();
        }
      }

      botonConfirmar.onclick = confirmar;
      if (botonCancelar) botonCancelar.onclick = () => cerrar(campo ? null : false);
      // Clic en el fondo también cancela, como en un AlertDialog.
      fondo.addEventListener('mousedown', (evento) => {
        if (evento.target === fondo) cerrar(campo ? null : false);
      });

      document.body.appendChild(fondo);
      document.body.style.overflow = 'hidden';
      document.addEventListener('keydown', alTeclear, true);

      // El foco arranca en el campo si hay, si no en el botón de confirmar.
      (control ?? botonConfirmar).focus();
      if (control) control.select?.();
    });
  }

  /** Confirmación simple: devuelve true o false. */
  function confirmar(opciones = {}) {
    return abrirDialogo({ variant: 'danger', ...opciones }).then((r) => r === true);
  }

  /** Pregunta simple: devuelve el texto escrito, o null si se canceló. */
  function preguntar(opciones = {}) {
    const campo = {
      etiqueta: opciones.etiqueta ?? 'Comentario',
      valor: opciones.valor ?? '',
      placeholder: opciones.placeholder ?? '',
      multilinea: opciones.multilinea ?? false,
      requerido: opciones.requerido ?? false,
      ayuda: opciones.ayuda ?? null,
    };
    return abrirDialogo({ variant: 'primary', campo, ...opciones })
      .then((r) => (r === false || r === null ? null : r));
  }

  /* ==========================================================================
   * TABS  ·  navegación con teclado como React Aria
   * ====================================================================== */

  /**
   * Conecta uno o varios tablists al mismo juego de paneles.
   *
   * La app tiene dos menús (el lateral de escritorio y el de móvil) que
   * controlan los mismos paneles, así que se pasan los dos: al activar una
   * pestaña, la equivalente del otro menú queda marcada también.
   *
   * Teclado (lo que React Aria resuelve por dentro del componente Tabs):
   *   · Enter / Espacio  → activa la pestaña enfocada
   *   · Flechas         → mueven a la pestaña vecina, en modo automático
   *   · Home / End      → primera y última
   * Solo la pestaña activa queda en el recorrido del Tab (roving tabindex),
   * que es lo que evita que se lean seis paradas inútiles.
   *
   * @param {object} opciones
   *   pestanas      NodeList (o array) de elementos [role="tab"]
   *   seleccionar   callback(vista) con la carga de datos de cada vista
   *   enFoco        boolean; mover el foco al activar (true en teclado)
   */
  function conectarTabs({ pestanas, seleccionar = null, enFoco = false }) {
    const lista = Array.from(pestanas);

    function activar(indice, moverFoco = false) {
      const total = lista.length;
      const nuevo = (indice + total) % total;
      lista.forEach((pestana, i) => {
        const activa = i === nuevo;
        pestana.setAttribute('aria-selected', String(activa));
        pestana.tabIndex = activa ? 0 : -1;   // roving tabindex
        if (activa && moverFoco && enFoco) pestana.focus();
        const panel = document.getElementById(pestana.getAttribute('aria-controls'));
        if (panel) panel.classList.toggle('oculto', !activa);
      });
      if (seleccionar) seleccionar(lista[nuevo].dataset.vista);
    }

    lista.forEach((pestana, i) => {
      pestana.addEventListener('click', () => activar(i, false));
      pestana.addEventListener('keydown', (evento) => {
        switch (evento.key) {
          case 'ArrowDown': case 'ArrowRight':
            evento.preventDefault(); activar(i + 1, true); break;
          case 'ArrowUp': case 'ArrowLeft':
            evento.preventDefault(); activar(i - 1, true); break;
          case 'Home':
            evento.preventDefault(); activar(0, true); break;
          case 'End':
            evento.preventDefault(); activar(lista.length - 1, true); break;
          case 'Enter': case ' ':
            evento.preventDefault(); activar(i, false); break;
          default: break;
        }
      });
    });

    return { activar };
  }

  /**
   * Sincroniza varios grupos de pestañas que comparten los mismos paneles
   * (menú lateral + menú móvil) y devuelve un activador único.
   */
  function conectarTabsSincronizados({ grupos, seleccionar = null }) {
    const listas = grupos.map((grupo) => Array.from(grupo));
    const pestanas = listas.flat();
    if (!pestanas.length) return { irA: () => {} };

    const buscarIndice = (vista) => pestanas.findIndex((p) => p.dataset.vista === vista);

    function irA(vista, moverFoco = false) {
      pestanas.forEach((pestana) => {
        const activa = pestana.dataset.vista === vista;
        pestana.setAttribute('aria-selected', String(activa));
        pestana.tabIndex = activa ? 0 : -1;
        if (activa && moverFoco) pestana.focus();
        const panel = document.getElementById(pestana.getAttribute('aria-controls'));
        if (panel) panel.classList.toggle('oculto', !activa);
      });
      if (seleccionar) seleccionar(vista);
    }

    listas.forEach((lista) => {
      lista.forEach((pestana, i) => {
        pestana.setAttribute('aria-selected', String(pestana.dataset.vista === 'panel'));
        pestana.tabIndex = pestana.dataset.vista === 'panel' ? 0 : -1;
        pestana.addEventListener('click', () => irA(pestana.dataset.vista, false));
        pestana.addEventListener('keydown', (evento) => {
          const total = lista.length;
          const mover = (indice) => {
            const destino = lista[(indice + total) % total];
            irA(destino.dataset.vista, true);
          };
          switch (evento.key) {
            case 'ArrowDown': case 'ArrowRight':
              evento.preventDefault(); mover(i + 1); break;
            case 'ArrowUp': case 'ArrowLeft':
              evento.preventDefault(); mover(i - 1); break;
            case 'Home':
              evento.preventDefault(); mover(0); break;
            case 'End':
              evento.preventDefault(); mover(total - 1); break;
            case 'Enter': case ' ':
              evento.preventDefault(); irA(pestana.dataset.vista, false); break;
            default: break;
          }
        });
      });
    });

    irA('panel', false);
    return { irA, indice: buscarIndice };
  }

  /* ==========================================================================
   * UTILIDADES COMPARTIDAS
   * ====================================================================== */

  /** Escapa texto para insertarlo como HTML. */
  function escapar(texto) {
    return String(texto ?? '').replace(/[&<>"']/g, (caracter) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[caracter]));
  }

  /** Conecta un botón hecho con HTML dentro de una tabla a su callback. */
  function delegar(contenedor, selector, manejador) {
    contenedor.querySelectorAll(selector).forEach((elemento) => {
      elemento.onclick = (evento) => manejador(elemento.dataset, elemento, evento);
    });
  }

  return {
    icono,
    // button
    clasesBoton, crearBoton, botonHTML,
    // badge
    clasesBadge, badge,
    // formularios
    campoHTML, inputHTML, selectHTML,
    // alert
    alertaHTML,
    // overlays
    toast, abrirDialogo, confirmar, preguntar,
    // navegación
    conectarTabs, conectarTabsSincronizados,
    // utilidades
    escapar, delegar,
  };
})();
