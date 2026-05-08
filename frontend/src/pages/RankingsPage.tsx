import React, { useEffect, useState } from "react";
import OverallRankingsTable from "../components/OverallRankingsTable";
import WaiverWireCheatSheet from "../components/WaiverWireCheatSheet";
import RisersFallers from "../components/RisersFallers";
import { Box, VStack } from "@chakra-ui/react";
import { api } from "../api/client";
import { OverallRankingsPayload } from "../types/player";

const RankingsPage: React.FC = () => {
  const [payload, setPayload] = useState<OverallRankingsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  // Tracked here so sibling components (waiver-wire cheat sheet) can stay in
  // sync with whatever scoring variant the rankings table is showing.
  const [variant, setVariant] = useState("halfppr_4ptpass");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getOverallRankings()
      .then((data) => {
        if (!cancelled) setPayload(data);
      })
      .catch((err) => {
        console.error("Error fetching rankings:", err);
        if (!cancelled) setPayload(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Box>
      <VStack align="stretch" gap={3} px={{ base: 2, md: 4 }} pt={4}>
        <WaiverWireCheatSheet variant={variant} />
        <RisersFallers variant={variant} />
      </VStack>
      <OverallRankingsTable
        rankings={payload?.overall_rankings ?? null}
        loading={loading}
        onVariantChange={setVariant}
      />
    </Box>
  );
};

export default RankingsPage;
