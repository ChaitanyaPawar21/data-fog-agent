"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { getCurrentUser, logout, type User } from "@/lib/auth";

const UserContext = createContext<User | null>(null);

/** The logged-in user. Only use inside <AppShell>, which waits until the user is loaded. */
export function useUser(): User {
  return useContext(UserContext)!;
}

/** Page frame for logged-in pages: checks the session, then shows the nav and the page. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    getCurrentUser().then((currentUser) => {
      if (currentUser) setUser(currentUser);
      else router.replace("/login");
    });
  }, [router]);

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  if (!user) return null;

  const links = [
    { href: "/chat", label: "Chat" },
    { href: "/catalog", label: "Catalogue" },
    { href: "/approvals", label: "Approvals" },
    { href: "/alerts", label: "Alerts" },
  ];
  if (user.role === "admin") links.push({ href: "/sources", label: "Sources" });
  links.push({ href: "/settings", label: "Settings" });

  return (
    <UserContext.Provider value={user}>
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b px-6 py-3">
        <span className="font-semibold">Data Fog Agent</span>
        <nav className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={
                pathname.startsWith(link.href)
                  ? "font-medium"
                  : "text-muted-foreground hover:text-foreground"
              }
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm">
          <span className="text-muted-foreground">{user.name}</span>
          <Button variant="outline" size="sm" onClick={handleLogout}>
            Log out
          </Button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 p-6">{children}</main>
    </UserContext.Provider>
  );
}
