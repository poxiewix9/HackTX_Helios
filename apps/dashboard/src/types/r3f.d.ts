// React Three Fiber v9 + React 19: register the three.js intrinsic elements with JSX.
import type { ThreeElements } from "@react-three/fiber";

declare module "react" {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX {
    // eslint-disable-next-line @typescript-eslint/no-empty-object-type
    interface IntrinsicElements extends ThreeElements {}
  }
}
