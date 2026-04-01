import { useState } from "react";
import { api } from "../api";
import type { BootstrapResponse, User } from "../types";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [usernameInput, setUsernameInput] = useState("");

  async function login(username: string): Promise<BootstrapResponse> {
    return api.bootstrap(username);
  }

  return {
    user,
    setUser,
    usernameInput,
    setUsernameInput,
    login,
  };
}
