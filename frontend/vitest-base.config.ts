import { defineConfig } from 'vitest/config';

// El primer test de cada fichero paga la compilación JIT de su componente.
// Con todos los ficheros en paralelo, los componentes grandes (Inicio, detalle,
// perfil) pasaban de los 5 s por defecto sin que fallara ninguna aserción.
export default defineConfig({ test: { testTimeout: 20_000 } });
