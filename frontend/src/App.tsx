import React from 'react';
import { ChakraProvider } from '@chakra-ui/react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import HomePage from './pages/HomePage';
import RankingsPage from './pages/RankingsPage';
import NavBar from './components/NavBar';
import {theme} from './theme'

const App: React.FC = () => {
  return (
    <ChakraProvider theme = {theme}>
      <Router>
        <NavBar />
        <Routes>
          <Route path = "/" element={<HomePage />} />
          <Route path = "/ranks" element={<RankingsPage />} />
        </Routes>
      </Router>
    </ChakraProvider>
  );
};

export default App;
