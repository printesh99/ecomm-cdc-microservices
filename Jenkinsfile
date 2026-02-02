pipeline {
  agent any

  environment {
    IMAGE_NS = "ecomm"

    // OpenShift internal registry (works from within cluster)
    REGISTRY = "image-registry.openshift-image-registry.svc:5000"

    // GitOps repo and path to overlay that contains image tags
    GITOPS_REPO = "https://github.com/printesh99/ecomm-cdc-gitops.git"
    GITOPS_BRANCH = "main"
    GITOPS_OVERLAY_PATH = "apps/overlays/dev/kustomization.yaml"
  }

  stages {
    stage("Checkout") {
      steps {
        checkout scm
        script {
          env.GIT_SHA = sh(script: "git rev-parse --short HEAD", returnStdout: true).trim()
          echo "GIT_SHA=${env.GIT_SHA}"
        }
      }
    }

    stage("Build & Push Images (OpenShift Builds)") {
      steps {
        script {
          def services = [
            [name:"catalog-service",  dir:"services/catalog"],
            [name:"cart-service",     dir:"services/cart"],
            [name:"orders-service",   dir:"services/orders"],
            [name:"payment-service",  dir:"services/payment"],
            [name:"shipping-service", dir:"services/shipping"]
          ]

          for (s in services) {
            sh """
              set -euo pipefail
              echo "==> Building ${s.name} from ${s.dir}"

              test -d "${s.dir}" || (echo "ERROR: missing dir ${s.dir}" && exit 2)

              # Create BC once (docker strategy, binary build)
              oc -n ${IMAGE_NS} get bc/${s.name} >/dev/null 2>&1 || \
                oc -n ${IMAGE_NS} new-build --name=${s.name} --strategy=docker --binary=true

              # Start build from directory
              oc -n ${IMAGE_NS} start-build ${s.name} --from-dir=${s.dir} --follow

              # Tag imagestream to git sha
              oc -n ${IMAGE_NS} tag ${s.name}:latest ${s.name}:${GIT_SHA}

              echo "==> ${s.name} tagged ${GIT_SHA}"
            """
          }
        }
      }
    }

    stage("Update GitOps (bump image tags)") {
      steps {
        withCredentials([usernamePassword(credentialsId: 'github-pat', usernameVariable: 'GIT_USER', passwordVariable: 'GIT_PAT')]) {
          sh """
            set -euo pipefail

            rm -rf /tmp/gitops && mkdir -p /tmp/gitops
            cd /tmp/gitops

            git clone ${GITOPS_REPO} repo
            cd repo
            git checkout ${GITOPS_BRANCH}

            test -f ${GITOPS_OVERLAY_PATH} || (echo "ERROR: missing ${GITOPS_OVERLAY_PATH}" && exit 3)

            # Update newTag for each service in kustomization.yaml
            for svc in catalog-service cart-service orders-service payment-service shipping-service; do
              python3 - <<'PY'
import sys, re, pathlib
svc = sys.argv[1]
sha = sys.argv[2]
path = pathlib.Path(sys.argv[3])
txt = path.read_text()

# find the block starting at "- name: <svc>" and replace its newTag line
pattern = r'(-\\s*name:\\s*%s\\s*\\n(?:.*\\n)*?\\s*newTag:\\s*)(.*)' % re.escape(svc)
m = re.search(pattern, txt)
if not m:
    print(f"WARN: service {svc} not found in images block")
else:
    # replace only the newTag value in that service block
    txt = re.sub(pattern, lambda mm: mm.group(1) + sha, txt, count=1)
    path.write_text(txt)
PY
              ${svc} ${GIT_SHA} ${GITOPS_OVERLAY_PATH}
            done

            git add ${GITOPS_OVERLAY_PATH}
            git commit -m "ci: bump images to ${GIT_SHA}" || echo "No changes to commit"

            git push https://${GIT_USER}:${GIT_PAT}@github.com/printesh99/ecomm-cdc-gitops.git ${GITOPS_BRANCH}
          """
        }
      }
    }
  }
}

