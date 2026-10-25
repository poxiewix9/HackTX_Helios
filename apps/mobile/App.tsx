import { StatusBar } from "expo-status-bar";

import { ArrowScreen } from "@/phone/ArrowScreen";
import { useDevice } from "@/phone/useDevice";

export default function App() {
  const [view, actions] = useDevice();
  return (
    <>
      <StatusBar style="light" />
      <ArrowScreen view={view} actions={actions} />
    </>
  );
}
