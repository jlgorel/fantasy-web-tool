import React from 'react';
import { ChakraProvider } from '@chakra-ui/react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import HomePage from './pages/HomePage';
import RankingsPage from './pages/RankingsPage';
import PlayerDetailPage from './pages/PlayerDetailPage';
import WrappedPage from './pages/WrappedPage';
import WrappedLandingPage from './pages/WrappedLandingPage';
import DraftHelpPage from './pages/DraftHelpPage';
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
          <Route path = "/wrapped" element={<WrappedLandingPage />} />
          <Route path = "/wrapped/sleeper/:leagueId" element={<WrappedPage />} />
          <Route path = "/draft-help" element={<DraftHelpPage />} />
          <Route path = "/player/:playerId" element={<PlayerDetailPage />} />
        </Routes>
      </Router>
    </ChakraProvider>
  );
};

export default App;
