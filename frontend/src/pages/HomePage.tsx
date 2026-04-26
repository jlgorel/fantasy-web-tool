import React, { useState, useEffect, useCallback } from "react";
import { Box, VStack, HStack, Image, Text, Spinner } from "@chakra-ui/react";
import { useUUID } from "../context/UUIDContext";
import DynamicTabs from "../components/LeagueTabs";
import UsernameForm from "../components/UsernameForm";
import { api } from "../api/client";
import { WebsiteName } from "../types/player";

const HomePage: React.FC = () => {
  const [name, setName] = useState<string>("");
  const [showTabs, setShowTabs] = useState<boolean>(false);
  const [showInstructions, setShowInstructions] = useState<boolean>(true);
  const [runtime, setRuntime] = useState("");
  const [loading, setLoading] = useState(false);
  const [website, setWebsite] = useState<WebsiteName>("Sleeper");

  const userUUID = useUUID();

  useEffect(() => {
    api
      .loadLastRunInfo()
      .then((data) => setRuntime((data.Runtime as string) ?? ""))
      .catch((err) => console.error(err));
  }, []);

  const handleSaveClick = useCallback(async () => {
    if (!userUUID || !name.trim()) return;

    setShowTabs(false);
    setShowInstructions(false);
    setLoading(true);

    try {
      await api.loadSleeperInfo(userUUID, name, website);
      setName("");
      setShowTabs(true);
    } catch (err) {
      console.error(err);
    } finally {
      // Ensure spinner shows for at least 150ms
      setTimeout(() => setLoading(false), 150);
    }
  }, [userUUID, name, website]);

  return (
    <Box minH="100vh" bgGradient="linear(to-b, gray.50, gray.100)" px={{ base: 2, md: 8 }} py={6}>
      <VStack align="stretch" gap={6} mt={6} px={{ base: 4, md: 16 }}>
        {/* Header */}
        <VStack gap={1} align="center">
          <HStack justify="center" gap={4} wrap="wrap">
            <Image src={`${process.env.PUBLIC_URL}/AmericanFootball.png`} boxSize={{ base: "60px", md: "100px" }} />
            <Text fontSize={{ base: "xl", md: "3xl" }} fontWeight="bold" textAlign="center">
              Fantasy Football Team Visualizer
            </Text>
            <Image src={`${process.env.PUBLIC_URL}/AmericanFootball.png`} boxSize={{ base: "60px", md: "100px" }} />
          </HStack>
          <Text fontSize="sm" color="gray.600">
            Data last updated at: {runtime || "Loading..."}
          </Text>
        </VStack>

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
            <UsernameForm
              website={website}
              onWebsiteChange={setWebsite}
              username={name}
              onUsernameChange={setName}
              onSubmit={handleSaveClick}
            />
          </VStack>
        )}

        {/* Tabs & Player Table */}
        {!showInstructions && (
          <VStack align="stretch" gap={4}>
            {loading ? (
              <VStack align="center" justify="center" minH="50vh">
                <Spinner size="xl" />
                <Text>Loading...</Text>
              </VStack>
            ) : (
              <>
                <DynamicTabs showTabs={showTabs} />
                <UsernameForm
                  website={website}
                  onWebsiteChange={setWebsite}
                  username={name}
                  onUsernameChange={setName}
                  onSubmit={handleSaveClick}
                  submitLabel="Reload Teams"
                />
              </>
            )}
          </VStack>
        )}
      </VStack>
    </Box>
  );
};

export default HomePage;
