import { SettingsNav } from "@/components/shell/SettingsNav";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-full overflow-y-auto scrollbar-quiet">
      <SettingsNav />
      <div className="max-w-4xl space-y-10 p-6">{children}</div>
    </div>
  );
}
