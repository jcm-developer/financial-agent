/**
 * Cliente HTTP de la API. Una sola puerta para todas las peticiones.
 *
 * Las rutas son **relativas** (`/api/...`) a proposito: en produccion la API
 * sirve el build desde su mismo origen (F3.7) y en desarrollo el proxy de Vite
 * reenvia `/api` a donde diga `VITE_API_TARGET`. Con una URL absoluta habria que
 * configurar el origen en dos sitios y mantenerlos de acuerdo.
 */

/** Un error que la API ha explicado. `status` es el codigo HTTP. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  /** Los 4xx son culpa de la peticion: reintentarla da el mismo resultado. */
  get isClientError() {
    return this.status >= 400 && this.status < 500;
  }
}

type Params = Record<string, string | number | boolean | undefined | null>;

interface Opciones {
  params?: Params;
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Saca una frase legible del cuerpo de error de FastAPI.
 *
 * Tiene dos formas y hay que cubrir las dos: `{"detail": "texto"}` en los
 * errores que lanzamos nosotros, y `{"detail": [{loc, msg}, …]}` en los 422 de
 * validacion de Pydantic. Sin aplanar el segundo, el formulario de 41 campos de
 * F6.8 diria "[object Object]" justo cuando el usuario necesita saber que campo
 * ha puesto mal.
 */
function mensajeDeError(status: number, cuerpo: unknown): string {
  if (typeof cuerpo === "string" && cuerpo.trim()) return cuerpo;

  if (cuerpo && typeof cuerpo === "object" && "detail" in cuerpo) {
    const detail = (cuerpo as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const partes = detail.map((entrada) => {
        if (!entrada || typeof entrada !== "object") return String(entrada);
        const { loc, msg } = entrada as { loc?: unknown[]; msg?: string };
        // `loc` empieza por "body"/"query", que no le dice nada a nadie: se
        // queda el resto, que es el nombre del campo.
        const campo = Array.isArray(loc) ? loc.slice(1).join(".") : "";
        return campo ? `${campo}: ${msg ?? ""}`.trim() : (msg ?? "");
      });
      if (partes.length) return partes.join("; ");
    }
  }
  return `La API respondio ${status}.`;
}

function construirUrl(path: string, params?: Params): string {
  if (!params) return path;
  const query = new URLSearchParams();
  for (const [clave, valor] of Object.entries(params)) {
    if (valor === undefined || valor === null || valor === "") continue;
    query.set(clave, String(valor));
  }
  const cadena = query.toString();
  return cadena ? `${path}?${cadena}` : path;
}

async function request<T>(
  method: string,
  path: string,
  { params, body, signal }: Opciones = {},
): Promise<T> {
  let respuesta: Response;
  try {
    respuesta = await fetch(construirUrl(path, params), {
      method,
      signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (causa) {
    // Aqui no hay respuesta: el servidor no esta, o la red se ha caido. Se
    // distingue del error con codigo porque la pantalla dice otra cosa.
    if (causa instanceof DOMException && causa.name === "AbortError") throw causa;
    throw new ApiError(0, "No se pudo contactar con la API.", causa);
  }

  if (respuesta.status === 204) return undefined as T;

  const texto = await respuesta.text();
  let cuerpo: unknown = texto;
  if (texto) {
    try {
      cuerpo = JSON.parse(texto);
    } catch {
      // Se queda el texto crudo: mejor un mensaje raro que tragarse el error.
    }
  }

  if (!respuesta.ok) {
    throw new ApiError(respuesta.status, mensajeDeError(respuesta.status, cuerpo), cuerpo);
  }
  return cuerpo as T;
}

export const api = {
  get: <T>(path: string, params?: Params, signal?: AbortSignal) =>
    request<T>("GET", path, { params, signal }),
  post: <T>(path: string, body?: unknown, params?: Params) =>
    request<T>("POST", path, { body, params }),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, { body }),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, { body }),
  delete: <T>(path: string, params?: Params) => request<T>("DELETE", path, { params }),
};
