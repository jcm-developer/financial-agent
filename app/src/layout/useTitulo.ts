import { useEffect } from "react";

/**
 * Título del documento por pantalla.
 *
 * En una aplicación de una sola página el título no cambia solo, y eso rompe dos
 * cosas: un lector de pantalla anuncia siempre lo mismo al navegar —así que no
 * hay forma de saber que la página cambió— y con varias pestañas abiertas del
 * mismo experimento todas se llaman igual.
 *
 * Lleva el nombre del perfil cuando lo hay, que es la misma razón por la que el
 * perfil va en la URL: saber qué experimento se está mirando sin deducirlo.
 */
export function useTitulo(seccion: string, perfil?: string) {
  useEffect(() => {
    document.title = [seccion, perfil, "financial-bot"].filter(Boolean).join(" · ");
  }, [seccion, perfil]);
}
