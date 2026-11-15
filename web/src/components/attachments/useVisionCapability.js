import { useEffect, useState } from 'react';
import { fetchVisionCapability } from '../../services/attachments';

/**
 * ¿Admite imágenes el modelo elegido y saldrían de esta máquina? Solo se consulta cuando hay una imagen en el mensaje. Devuelve
 * `null` hasta saberlo; el servidor es quien manda (el servidor vuelve a comprobarlo al iniciar la misión).
 */
export default function useVisionCapability(active, provider, model, baseUrl) {
  const [state, setState] = useState({ key: '', data: null });
  const key = active ? `${provider}|${model}|${baseUrl}` : '';

  useEffect(() => {
    if (!key) return undefined;
    let current = true;
    fetchVisionCapability({ provider, model, baseUrl }).then((data) => {
      if (current) setState({ key, data });
    });
    return () => { current = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  return state.key === key && key ? state.data : null;
}
