import React from 'react';
import { ChakraProvider } from '@chakra-ui/react';
import HomePage from './pages/HomePage';
import {theme} from './theme'

const App: React.FC = () => {
  return (
    <ChakraProvider theme = {theme}>
      <HomePage />
    </ChakraProvider>
  );
};

export default App;
