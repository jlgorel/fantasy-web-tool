import React, { createContext, useContext, useMemo } from 'react';
import { v4 as uuidv4 } from 'uuid';

const UUIDContext = createContext<string | undefined>(undefined);

export const UUIDProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const uuid = useMemo(() => uuidv4(), []);

  console.log("UUIDProvider is rendering with UUID:", uuid);

  return (
    <UUIDContext.Provider value={uuid}>
      {children}
    </UUIDContext.Provider>
  );
};

export const useUUID = () => {
  const context = useContext(UUIDContext);
  if (context === undefined) {
    throw new Error('useUUID must be used within a UUIDProvider');
  }
  return context;
};
