import React, { createContext, useContext, useMemo } from 'react';
import { validate as validateUUID, v4 as uuidv4 } from 'uuid';

const UUIDContext = createContext<string | undefined>(undefined);
export const UUID_STORAGE_KEY = 'fantasy-web-tool-user-id-v1';

const getOrCreateUUID = (): string => {
  let storage: Storage | undefined;

  try {
    storage = window.localStorage;
    const storedUUID = storage.getItem(UUID_STORAGE_KEY);
    if (storedUUID !== null && validateUUID(storedUUID)) {
      return storedUUID;
    }
  } catch {
    // Continue with an in-memory ID when localStorage is inaccessible.
  }

  const uuid = uuidv4();
  try {
    storage?.setItem(UUID_STORAGE_KEY, uuid);
  } catch {
    // The provider remains usable when localStorage writes are blocked.
  }
  return uuid;
};

export const UUIDProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const uuid = useMemo(getOrCreateUUID, []);

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
