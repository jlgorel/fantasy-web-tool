import React, { useState, useEffect } from 'react';
import { useUUID } from '../context/UUIDContext';
import DynamicTabs from '../components/LeagueTabs';
import { Box, VStack, HStack, Image, Input, Button, Text, Spinner } from '@chakra-ui/react';

if (!process.env.REACT_APP_API_BASE_URL) {
  throw new Error("REACT_APP_API_BASE_URL is not set!");
}
export const API_BASE = process.env.REACT_APP_API_BASE_URL;

const HomePage: React.FC = () => {
  const [name, setName] = useState<string>('');
  const [showTabs, setShowTabs] = useState<boolean>(false);
  const [showInstructions, setShowInstructions] = useState<boolean>(true);
  const [runtime, setRuntime] = useState('');
  const [loading, setLoading] = useState(false); // lift loading here

  const userUUID = useUUID();

  useEffect(() => {
    const loadLastRunInfo = async () => {
      try {
        const response = await fetch(API_BASE + '/load-last-run-info');
        const data = await response.json();
        setRuntime(data['Runtime']);
      } catch (err) {
        console.error(err);
      }
    };
    loadLastRunInfo();
  }, []);

  const handleSaveClick = async () => {
    if (!userUUID) return;

    setShowTabs(false);
    setShowInstructions(false);
    setLoading(true); // spinner appears instantly

    try {
      const response = await fetch(API_BASE + '/load-sleeper-info', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-UUID': userUUID,
        },
        body: JSON.stringify({ name }),
      });

      if (response.ok) {
        await response.json();
        setName('');
        setShowTabs(true);
      } else {
        console.error('Failed to get data for user');
      }
    } catch (err) {
      console.error(err);
    } finally {
      // Ensure spinner shows for at least 150ms
      setTimeout(() => setLoading(false), 150);
    }
  };

  return (
    <Box minH="100vh" bgGradient="linear(to-b, gray.50, gray.100)" px={{ base: 2, md: 8 }} py={6}>
      <VStack align="stretch" gap={6} mt={6} px={{ base: 4, md: 16 }}>
        {/* Header */}
        <HStack justify="center" position="relative" gap={4}>
          <Image src={`${process.env.PUBLIC_URL}/AmericanFootball.png`} boxSize="100px" />
          <Text fontSize="3xl" fontWeight="bold" textAlign="center">
            Fantasy Football Team Visualizer
          </Text>
          <Image src={`${process.env.PUBLIC_URL}/AmericanFootball.png`} boxSize="100px" />
          <Text
            position="absolute"
            top={0}
            right={0}
            bg="whiteAlpha.800"
            px={2}
            py={1}
            borderRadius="md"
            fontSize="sm"
          >
            Data last updated at: {runtime || 'Loading...'}
          </Text>
        </HStack>

        {/* Instructions / Input */}
        {showInstructions && (
          <VStack align="stretch" gap={4}>
            <Text fontSize="xl" fontWeight="bold" textAlign="center">
              Enter your Sleeper username to load your fantasy football teams!
            </Text>
            <Text fontSize="sm">
              The app loads all rosters with projections. IDP not supported. Boris Chen tiers
              build the ideal lineup. Vegas-projected points are shown for each player. Matchup
              rating 5 stars = great matchup, 1 star = tough matchup. Enjoy!
            </Text>
            <HStack gap={2} justify="center">
              <Input
                placeholder="Enter Sleeper username"
                value={name}
                onChange={e => setName(e.target.value)}
                maxW="300px"
              />
              <Button onClick={handleSaveClick} colorScheme="blue">
                Load Teams
              </Button>
            </HStack>
          </VStack>
        )}

        {/* Tabs & Player Table */}
        {!showInstructions && (
          <VStack align="stretch" gap={4}>
            <HStack gap={2} justify="center">
              <Input
                placeholder="Enter Sleeper username"
                value={name}
                onChange={e => setName(e.target.value)}
                maxW="300px"
              />
              <Button onClick={handleSaveClick} colorScheme="blue">
                Load Teams
              </Button>
            </HStack>

            {loading ? (
              <VStack align="center" justify="center" minH="50vh">
                <Spinner size="xl" />
                <Text>Loading...</Text>
              </VStack>
            ) : (
              <DynamicTabs showTabs={showTabs} />
            )}
          </VStack>
        )}
      </VStack>
    </Box>
  );
};

export default HomePage;
