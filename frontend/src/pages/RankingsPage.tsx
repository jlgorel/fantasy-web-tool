import React, { useEffect, useState } from "react";
import OverallRankingsTable from "../components/OverallRankingsTable";
import { Box } from "@chakra-ui/react";
import { api } from "../api/client";
import { OverallRankingsPayload } from "../types/player";

const RankingsPage: React.FC = () => {
  const [payload, setPayload] = useState<OverallRankingsPayload | null>(null);
  const [loading, setLoading] = useState(true);

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
      <OverallRankingsTable
        rankings={payload?.overall_rankings ?? null}
        loading={loading}
      />
    </Box>
  );
};

export default RankingsPage;
