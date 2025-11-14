// src/pages/RankingsPage.tsx
import React, { useState, useEffect } from 'react';
import { useUUID } from '../context/UUIDContext';
import PlayerTable from '../components/PlayerTable';
import { Box, VStack, HStack, Text, Button, Spinner } from '@chakra-ui/react';

if (!process.env.REACT_APP_API_BASE_URL) {
  throw new Error("REACT_APP_API_BASE_URL is not set!");
}
export const API_BASE = process.env.REACT_APP_API_BASE_URL;

const RankingsPage: React.FC = () => {
  const userUUID = useUUID();

  const [pprSetting, setPprSetting] = useState<'STD' | '0.5' | '1'>('1');
  const [passTD, setPassTD] = useState<4 | 6>(6);

  const [allRankings, setAllRankings] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Compute key for selected scoring
  const selectedKey = `${pprSetting === '1' ? 'fullppr' : pprSetting === '0.5' ? 'halfppr' : 'std'}_${passTD}ptpass`;

  const fetchAllRanks = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/overall-ranks`, {
        headers: { 'X-User-UUID': userUUID },
      });
      if (!response.ok) throw new Error('Failed to fetch overall ranks');
      const data = await response.json();
      setAllRankings(data);
    } catch (err) {
      console.error(err);
      setError('Failed to fetch overall ranks');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAllRanks();
  }, []);

  const rankingsData = allRankings[selectedKey] ?? [];

  return (
    <Box minH="100vh" px={{ base: 2, md: 8 }} py={6} bg="gray.50">
      <VStack align="stretch" gap={6} px={{ base: 4, md: 16 }}>
        <Text fontSize="3xl" fontWeight="bold" textAlign="center">
          Overall Fantasy Rankings
        </Text>

        {/* Scoring settings */}
        <HStack justify="center" gap={4}>
          <Text>PPR:</Text>
          <Button
            onClick={() => setPprSetting('STD')}
            colorScheme={pprSetting === 'STD' ? 'blue' : 'gray'}
          >
            STD
          </Button>
          <Button
            onClick={() => setPprSetting('0.5')}
            colorScheme={pprSetting === '0.5' ? 'blue' : 'gray'}
          >
            0.5
          </Button>
          <Button
            onClick={() => setPprSetting('1')}
            colorScheme={pprSetting === '1' ? 'blue' : 'gray'}
          >
            1
          </Button>

          <Text>Passing TD:</Text>
          <Button
            onClick={() => setPassTD(4)}
            colorScheme={passTD === 4 ? 'blue' : 'gray'}
          >
            4
          </Button>
          <Button
            onClick={() => setPassTD(6)}
            colorScheme={passTD === 6 ? 'blue' : 'gray'}
          >
            6
          </Button>
        </HStack>

        {/* Loading / Error / Table */}
        {loading ? (
          <VStack align="center" justify="center" minH="50vh">
            <Spinner size="xl" />
            <Text>Loading rankings...</Text>
          </VStack>
        ) : error ? (
          <Text color="red.500" textAlign="center">
            {error}
          </Text>
        ) : (
          <PlayerTable data={rankingsData} />
        )}
      </VStack>
    </Box>
  );
};

export default RankingsPage;
