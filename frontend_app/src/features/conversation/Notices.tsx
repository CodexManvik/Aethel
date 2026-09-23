import { useEffect } from "react";
import { toast } from "sonner";
import { useSession } from "../../stores/session";

/** Turns session notices (provider switches, protocol errors) into quiet toasts. */
export function Notices() {
  const notices = useSession((s) => s.notices);
  useEffect(() => {
    notices.forEach((n) => {
      toast(n.text);
      useSession.getState().dismissNotice(n.id);
    });
  }, [notices]);
  return null;
}
