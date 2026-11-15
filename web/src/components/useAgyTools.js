import { useEffect, useState } from 'react';
import { fetchAgyTools } from '../services/agyTools';

/** Catálogo de herramientas de agy del servidor (solo se pide cuando el proveedor elegido es agy). `loaded` indica que ya respondió o falló. */
export default function useAgyTools(active) {
  const [state, setState] = useState({ data: null, loaded: false });
  useEffect(() => {
    if (!active) return undefined;
    let current = true;
    fetchAgyTools().then((data) => { if (current) setState({ data, loaded: true }); });
    return () => { current = false; };
  }, [active]);
  return state;
}
