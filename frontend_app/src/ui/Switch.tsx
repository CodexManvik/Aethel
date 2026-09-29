import * as RadixSwitch from "@radix-ui/react-switch";

interface Props {
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  label: string;
  disabled?: boolean;
}

export function Switch({ checked, onCheckedChange, label, disabled }: Props) {
  return (
    <RadixSwitch.Root
      checked={checked}
      disabled={disabled}
      onCheckedChange={onCheckedChange}
      aria-label={label}
      className="relative h-6 w-11 shrink-0 rounded-full border border-hairline bg-well transition-colors data-[state=checked]:border-accent data-[state=checked]:bg-accent disabled:opacity-50"
    >
      <RadixSwitch.Thumb className="block size-5 translate-x-0.5 rounded-full bg-paper shadow transition-transform duration-[var(--dur-base)] ease-[var(--ease-out)] data-[state=checked]:translate-x-[22px]" />
    </RadixSwitch.Root>
  );
}
