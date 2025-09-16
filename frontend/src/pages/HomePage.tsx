import React, { useState, useEffect } from 'react';
import { useUUID } from '../context/UUIDContext';
import DynamicTabs from '../components/LeagueTabs';
import { Box, VStack, HStack, Image, Input, Button, Text, Spinner, } from '@chakra-ui/react';
import {
  Menu,
  MenuButton,
  MenuList,
  MenuItem,
} from "@chakra-ui/react";
import { ChevronDownIcon } from "@chakra-ui/icons";

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
  const [website, setWebsite] = useState<'Sleeper' | 'Fleaflicker'>('Sleeper');


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
        body: JSON.stringify({ name, website }),
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
              Choose your league type and enter your username to load your fantasy football teams!
            </Text>
            <Text fontSize="sm" color="gray.700" textAlign="center">
              This tool automatically suggests starters based on <b>Boris Chen tiers</b>, 
              shows <b>Vegas-projected points</b> for each player, and includes <b>Fantasy Pros matchup ratings</b>.
            </Text>
            <Text fontSize="sm" color="gray.700" textAlign="center" whiteSpace="pre-line">
              Click on a player card to expand exact vegas projections for each stat and see <b>boom/bust probabilities</b> based on your leagues settings.<br></br>
              <b>Spot start top free agents</b> based on vegas-projections are displayed below your starters for quick reference.
            </Text>
            <HStack gap={2} justify="center">
            <Menu>
              <MenuButton
                as={Button}
                rightIcon={<ChevronDownIcon />}
                bg={website === "Sleeper" ? "#0f2030" : "#ffffff"}         // dynamic background
                color={website === "Sleeper" ? "white" : "black"}         // dynamic text color
                _hover={{ bg: website === "Sleeper" ? "#1a3550" : "#f0f4f8" }}
                _active={{ bg: website === "Sleeper" ? "#1a3550" : "#f0f4f8" }}
                _focus={{ boxShadow: "none" }}                             // remove focus border
                border="none"
                px={3}
                py={1}
                borderRadius="md"
              >
                <HStack spacing={2}>
                  <Image
                    src={
                      website === "Sleeper"
                        ? `${process.env.PUBLIC_URL}/SleeperLogo.png`
                        : `${process.env.PUBLIC_URL}/FleaFlickerLogo.jpg`
                    }
                    boxSize="20px"
                  />
                  <Text>{website}</Text>
                </HStack>
              </MenuButton>

              <MenuList minW="unset" w="auto" bg="transparent" boxShadow="none" p={0}>
                <MenuItem
                  onClick={() => setWebsite("Sleeper")}
                  bg="#0f2030"
                  _hover={{ bg: "#1a3550" }}
                  borderRadius="md"
                >
                  <HStack spacing={2}>
                    <Image src={`${process.env.PUBLIC_URL}/SleeperLogo.png`} boxSize="20px" />
                    <Text color="white">Sleeper</Text>
                  </HStack>
                </MenuItem>

                <MenuItem
                  onClick={() => setWebsite("Fleaflicker")}
                  bg="#ffffff"
                  _hover={{ bg: "#f0f4f8" }}
                  borderRadius="md"
                >
                  <HStack spacing={2}>
                    <Image src={`${process.env.PUBLIC_URL}/FleaFlickerLogo.jpg`} boxSize="20px" />
                    <Text color="black">Fleaflicker</Text>
                  </HStack>
                </MenuItem>
              </MenuList>
            </Menu>

              <Input
                placeholder={website === 'Sleeper' ? "Enter Sleeper username" : "Enter your Fleaflicker email"}
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
            <Menu>
              <MenuButton
                as={Button}
                rightIcon={<ChevronDownIcon />}
                bg={website === "Sleeper" ? "#0f2030" : "#ffffff"}         // dynamic background
                color={website === "Sleeper" ? "white" : "black"}         // dynamic text color
                _hover={{ bg: website === "Sleeper" ? "#1a3550" : "#f0f4f8" }}
                _active={{ bg: website === "Sleeper" ? "#1a3550" : "#f0f4f8" }}
                _focus={{ boxShadow: "none" }}                             // remove focus border
                px={3}
                py={1}
                borderRadius="md"
              >
                <HStack spacing={2}>
                  <Image
                    src={
                      website === "Sleeper"
                        ? `${process.env.PUBLIC_URL}/SleeperLogo.png`
                        : `${process.env.PUBLIC_URL}/FleaFlickerLogo.jpg`
                    }
                    boxSize="20px"
                  />
                  <Text>{website}</Text>
                </HStack>
              </MenuButton>

              <MenuList minW="unset" w="auto" bg="transparent" boxShadow="none" p={0}>
                <MenuItem
                  onClick={() => setWebsite("Sleeper")}
                  bg="#0f2030"
                  _hover={{ bg: "#1a3550" }}
                  borderRadius="md"
                >
                  <HStack spacing={2}>
                    <Image src={`${process.env.PUBLIC_URL}/SleeperLogo.png`} boxSize="20px" />
                    <Text color="white">Sleeper</Text>
                  </HStack>
                </MenuItem>

                <MenuItem
                  onClick={() => setWebsite("Fleaflicker")}
                  bg="#ffffff"
                  _hover={{ bg: "#f0f4f8" }}
                  borderRadius="md"
                >
                  <HStack spacing={2}>
                    <Image src={`${process.env.PUBLIC_URL}/FleaFlickerLogo.jpg`} boxSize="20px" />
                    <Text color="black">Fleaflicker</Text>
                  </HStack>
                </MenuItem>
              </MenuList>
            </Menu>

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
