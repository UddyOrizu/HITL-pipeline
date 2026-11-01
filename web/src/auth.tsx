import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken } from "./api";

// roles: what the user was given. permissions: what they can do (admin implies every role except reviewer).
export type Me = {
  id: string; email: string; name: string; roles: string[]; permissions: string[];
  tenant: { id: string; name: string } | null;
};

type AuthState = {
  me: Me | null;
  loading: boolean;
  signIn: (token: string) => Promise<void>;
  signOut: () => void;
  has: (...roles: string[]) => boolean;
};

const AuthContext = createContext<AuthState>(null as unknown as AuthState);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    try {
      setMe(await api<Me>("/v1/me"));
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const value: AuthState = {
    me,
    loading,
    signIn: async (token) => {
      setToken(token);
      setLoading(true);
      await load();
    },
    signOut: () => {
      setToken(null);
      setMe(null);
    },
    has: (...roles) => !!me && roles.some((r) => (me.permissions || me.roles).includes(r)),
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
