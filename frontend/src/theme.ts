// theme.ts
import { extendTheme } from '@chakra-ui/react';

export const theme = extendTheme({
  fonts: { heading: 'Inter, system-ui, sans-serif', body: 'Inter, system-ui, sans-serif' },
  colors: {
    brand: {
      50: '#eef6ff',
      500: '#2b6cb0', // primary
      700: '#184e7e',
    },
  },
  styles: {
    global: {
      body: { bg: 'gray.50', color: 'gray.800' },
    },
  },
});
